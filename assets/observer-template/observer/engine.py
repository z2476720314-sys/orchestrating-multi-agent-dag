"""Thread-safe snapshot polling and fan-out for the local observer."""

from __future__ import annotations

import copy
import hashlib
import json
import queue
import threading
from collections.abc import Callable
from typing import Any, cast

from .model import ObserverConfig

SnapshotCollector = Callable[[object], object]
SnapshotQueue = queue.Queue[dict[str, object]]


def _collect_snapshot(config: object) -> object:
    # Import lazily so tests and embedders can inject a collector independently
    # of optional local source availability.
    from . import collect_snapshot

    return collect_snapshot(cast(ObserverConfig, config))


def _as_dict(snapshot: object) -> dict[str, object]:
    if isinstance(snapshot, dict):
        return copy.deepcopy(snapshot)
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("snapshot must be a dict or provide to_dict()")
    value = to_dict()
    if not isinstance(value, dict):
        raise TypeError("snapshot.to_dict() must return a dict")
    return copy.deepcopy(value)


def _without_volatile_timestamps(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_volatile_timestamps(item)
            for key, item in value.items()
            if key not in {"generated_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_without_volatile_timestamps(item) for item in value]
    return value


def _content_digest(snapshot: dict[str, object]) -> str:
    comparable = _without_volatile_timestamps(snapshot)
    payload = json.dumps(
        comparable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_for_item(collection: str, item: dict[str, Any]) -> str | None:
    source = item.get("source")
    if isinstance(source, str):
        return source.lower()
    item_id = item.get("id")
    if isinstance(item_id, str) and ":" in item_id:
        return item_id.partition(":")[0].lower()
    if collection == "agents":
        kind = item.get("kind")
        if isinstance(kind, str):
            return kind.lower()
    return None


def _retain_failed_sources(
    previous: dict[str, object] | None, current: dict[str, object]
) -> dict[str, object]:
    """Keep prior source records when a new snapshot marks that source degraded."""
    if previous is None:
        return current

    unhealthy: set[str] = set()
    health = current.get("source_health", [])
    if isinstance(health, list):
        for item in health:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            status = item.get("status")
            if isinstance(source, str) and status not in {"live", "ok", "healthy"}:
                unhealthy.add(source.lower())

    if not unhealthy:
        return current

    merged = copy.deepcopy(current)
    for collection in ("agents", "tasks", "events"):
        old_items = previous.get(collection, [])
        new_items = current.get(collection, [])
        if not isinstance(old_items, list) or not isinstance(new_items, list):
            continue
        retained = [
            copy.deepcopy(item)
            for item in old_items
            if isinstance(item, dict)
            and _source_for_item(collection, item) in unhealthy
        ]
        fresh = [
            copy.deepcopy(item)
            for item in new_items
            if not (
                isinstance(item, dict)
                and _source_for_item(collection, item) in unhealthy
            )
        ]
        merged[collection] = fresh + retained
    return merged


class ObserverEngine:
    """Poll normalized snapshots and broadcast only meaningful changes."""

    def __init__(
        self,
        config: object,
        *,
        collector: SnapshotCollector | None = None,
        poll_seconds: float = 1.0,
        subscriber_queue_size: int = 8,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if subscriber_queue_size <= 0:
            raise ValueError("subscriber_queue_size must be positive")
        self.config = config
        self._collector = collector or _collect_snapshot
        self._poll_seconds = poll_seconds
        self._subscriber_queue_size = subscriber_queue_size
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._subscribers: set[SnapshotQueue] = set()
        self._thread: threading.Thread | None = None
        self._snapshot: object | None = None
        self._snapshot_dict: dict[str, object] | None = None
        self._digest: str | None = None
        self._last_error: Exception | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()

        # The initial value is available before start() returns. This avoids an
        # avoidable 503/SSE race during server startup.
        self.poll_once()
        thread = threading.Thread(
            target=self._run,
            name="observer-snapshot-engine",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (
                thread is None or not thread.is_alive()
            ):
                self._thread = None
            self._subscribers.clear()

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            self.poll_once()

    def poll_once(self) -> bool:
        """Collect once; return whether consumer-visible content changed."""
        try:
            raw_snapshot = self._collector(self.config)
            current = _as_dict(raw_snapshot)
        except Exception as exc:  # noqa: BLE001 - isolates arbitrary adapters
            with self._lock:
                self._last_error = exc
            return False

        with self._lock:
            current = _retain_failed_sources(self._snapshot_dict, current)
            digest = _content_digest(current)
            changed = digest != self._digest
            self._snapshot = raw_snapshot
            self._snapshot_dict = current
            self._digest = digest
            self._last_error = None
            subscribers = tuple(self._subscribers) if changed else ()

        if changed:
            for subscriber in subscribers:
                self._offer_latest(subscriber, current)
        return changed

    @staticmethod
    def _offer_latest(
        subscriber: SnapshotQueue, snapshot: dict[str, object]
    ) -> None:
        value = copy.deepcopy(snapshot)
        try:
            subscriber.put_nowait(value)
            return
        except queue.Full:
            pass
        try:
            subscriber.get_nowait()
        except queue.Empty:
            pass
        try:
            subscriber.put_nowait(value)
        except queue.Full:
            # Another publisher cannot exist, but a concurrent consumer can
            # make queue state change between operations. Dropping is safe.
            pass

    def subscribe(self) -> SnapshotQueue:
        subscriber: SnapshotQueue = queue.Queue(self._subscriber_queue_size)
        with self._lock:
            self._subscribers.add(subscriber)
            snapshot = self._snapshot_dict
            if snapshot is not None:
                self._offer_latest(subscriber, snapshot)
        return subscriber

    def unsubscribe(self, subscriber: SnapshotQueue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def snapshot_dict(self) -> dict[str, object]:
        with self._lock:
            if self._snapshot_dict is None:
                raise RuntimeError("snapshot is not available")
            return copy.deepcopy(self._snapshot_dict)

    @property
    def snapshot(self) -> object | None:
        with self._lock:
            return self._snapshot

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()
