import queue
import threading
import time
import unittest
from dataclasses import dataclass, field

from observer.engine import ObserverEngine


@dataclass(frozen=True)
class FakeSnapshot:
    generated_at: str
    workspace: str = r"C:\workspace"
    connection: str = "live"
    agents: tuple[dict[str, object], ...] = field(default_factory=tuple)
    tasks: tuple[dict[str, object], ...] = field(default_factory=tuple)
    events: tuple[dict[str, object], ...] = field(default_factory=tuple)
    source_health: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1",
            "generated_at": self.generated_at,
            "workspace": self.workspace,
            "connection": self.connection,
            "agents": [dict(item) for item in self.agents],
            "tasks": [dict(item) for item in self.tasks],
            "events": [dict(item) for item in self.events],
            "source_health": [dict(item) for item in self.source_health],
        }


class MutableCollector:
    def __init__(self, snapshot: FakeSnapshot):
        self.snapshot = snapshot
        self.calls = 0
        self.error: Exception | None = None
        self.called = threading.Event()

    def __call__(self, _config: object) -> FakeSnapshot:
        self.calls += 1
        self.called.set()
        if self.error is not None:
            raise self.error
        return self.snapshot


class BlockingCollector:
    def __init__(self, snapshot: FakeSnapshot):
        self.snapshot = snapshot
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, _config: object) -> FakeSnapshot:
        self.calls += 1
        if self.calls > 1:
            self.entered.set()
            self.release.wait(timeout=2)
        return self.snapshot


class CoordinatedOfferEngine(ObserverEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial_offer_started = threading.Event()
        self.release_initial_offer = threading.Event()
        self.changed_offer_completed = threading.Event()

    def _offer_latest(self, subscriber, snapshot):
        if snapshot.get("connection") == "live":
            self.initial_offer_started.set()
            self.release_initial_offer.wait(timeout=1)
            ObserverEngine._offer_latest(subscriber, snapshot)
            return
        ObserverEngine._offer_latest(subscriber, snapshot)
        self.changed_offer_completed.set()


class ObserverEngineTests(unittest.TestCase):
    def test_start_collects_initial_snapshot_and_seeds_new_subscriber(self):
        expected = FakeSnapshot("2026-09-11T10:00:00+00:00")
        engine = ObserverEngine(object(), collector=MutableCollector(expected), poll_seconds=60)
        self.addCleanup(engine.stop)

        engine.start()
        subscriber = engine.subscribe()

        self.assertEqual(expected.to_dict(), engine.snapshot_dict())
        self.assertEqual(expected.to_dict(), subscriber.get(timeout=0.2))

    def test_generated_at_only_change_is_not_broadcast(self):
        collector = MutableCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        engine = ObserverEngine(object(), collector=collector, poll_seconds=60)
        self.addCleanup(engine.stop)
        engine.start()
        subscriber = engine.subscribe()
        subscriber.get(timeout=0.2)

        collector.snapshot = FakeSnapshot("2026-09-11T10:00:01+00:00")
        changed = engine.poll_once()

        self.assertFalse(changed)
        with self.assertRaises(queue.Empty):
            subscriber.get(timeout=0.05)

    def test_nested_updated_at_only_change_is_not_broadcast(self):
        collector = MutableCollector(
            FakeSnapshot(
                "2026-09-11T10:00:00+00:00",
                agents=(
                    {
                        "id": "workbuddy:1",
                        "kind": "workbuddy",
                        "status": "running",
                        "updated_at": "2026-09-11T10:00:00+00:00",
                    },
                ),
                source_health=(
                    {
                        "source": "workbuddy",
                        "status": "healthy",
                        "updated_at": "2026-09-11T10:00:00+00:00",
                    },
                ),
            )
        )
        engine = ObserverEngine(object(), collector=collector, poll_seconds=60)
        self.addCleanup(engine.stop)
        engine.start()
        subscriber = engine.subscribe()
        subscriber.get(timeout=0.2)

        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00",
            agents=(
                {
                    "id": "workbuddy:1",
                    "kind": "workbuddy",
                    "status": "running",
                    "updated_at": "2026-09-11T10:00:01+00:00",
                },
            ),
            source_health=(
                {
                    "source": "workbuddy",
                    "status": "healthy",
                    "updated_at": "2026-09-11T10:00:01+00:00",
                },
            ),
        )

        self.assertFalse(engine.poll_once())
        with self.assertRaises(queue.Empty):
            subscriber.get(timeout=0.05)

    def test_content_change_is_broadcast(self):
        collector = MutableCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        engine = ObserverEngine(object(), collector=collector, poll_seconds=60)
        self.addCleanup(engine.stop)
        engine.start()
        subscriber = engine.subscribe()
        subscriber.get(timeout=0.2)

        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00",
            agents=({"id": "codex:1", "kind": "codex", "status": "running"},),
        )

        self.assertTrue(engine.poll_once())
        self.assertEqual("running", subscriber.get(timeout=0.2)["agents"][0]["status"])

    def test_collection_failure_retains_last_good_snapshot_and_keeps_polling(self):
        initial = FakeSnapshot(
            "2026-09-11T10:00:00+00:00",
            agents=({"id": "dsh:1", "kind": "dsh", "status": "running"},),
        )
        collector = MutableCollector(initial)
        engine = ObserverEngine(object(), collector=collector, poll_seconds=0.01)
        self.addCleanup(engine.stop)
        engine.start()

        collector.error = OSError("temporarily unavailable")
        before = collector.calls
        deadline = time.monotonic() + 0.5
        while collector.calls < before + 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertGreaterEqual(collector.calls, before + 2)
        self.assertEqual(initial.to_dict(), engine.snapshot_dict())
        self.assertIsInstance(engine.last_error, OSError)

    def test_degraded_source_retains_only_that_sources_last_good_records(self):
        collector = MutableCollector(
            FakeSnapshot(
                "2026-09-11T10:00:00+00:00",
                agents=(
                    {"id": "dsh:1", "kind": "dsh", "status": "running"},
                    {"id": "codex:1", "kind": "codex", "status": "running"},
                ),
                source_health=(
                    {"source": "dsh", "status": "live"},
                    {"source": "codex", "status": "live"},
                ),
            )
        )
        engine = ObserverEngine(object(), collector=collector, poll_seconds=60)
        self.addCleanup(engine.stop)
        engine.start()

        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00",
            agents=({"id": "codex:1", "kind": "codex", "status": "completed"},),
            source_health=(
                {"source": "dsh", "status": "degraded"},
                {"source": "codex", "status": "live"},
            ),
        )
        engine.poll_once()

        agents = {item["id"]: item for item in engine.snapshot_dict()["agents"]}
        self.assertEqual("running", agents["dsh:1"]["status"])
        self.assertEqual("completed", agents["codex:1"]["status"])

    def test_coordination_agent_ownership_uses_its_id_prefix(self):
        collector = MutableCollector(
            FakeSnapshot(
                "2026-09-11T10:00:00+00:00",
                agents=(
                    {
                        "id": "coordination:codex",
                        "kind": "codex",
                        "status": "running",
                    },
                ),
                source_health=(
                    {"source": "coordination", "status": "healthy"},
                ),
            )
        )
        engine = ObserverEngine(object(), collector=collector, poll_seconds=60)
        self.addCleanup(engine.stop)
        engine.start()

        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00",
            source_health=(
                {"source": "coordination", "status": "degraded"},
            ),
        )
        engine.poll_once()

        self.assertEqual(
            "coordination:codex", engine.snapshot_dict()["agents"][0]["id"]
        )

    def test_unsubscribe_and_stop_clean_up_subscribers(self):
        engine = ObserverEngine(
            object(), collector=MutableCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        )
        self.addCleanup(engine.stop)
        engine.start()
        first = engine.subscribe()
        engine.subscribe()

        self.assertEqual(2, engine.subscriber_count)
        engine.unsubscribe(first)
        self.assertEqual(1, engine.subscriber_count)

        engine.stop()
        self.assertEqual(0, engine.subscriber_count)
        self.assertFalse(engine.is_running)

    def test_stop_timeout_keeps_live_thread_reference_and_prevents_restart(self):
        collector = BlockingCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        engine = ObserverEngine(object(), collector=collector, poll_seconds=0.01)
        starter: threading.Thread | None = None
        try:
            engine.start()
            self.assertTrue(collector.entered.wait(timeout=0.5))

            engine.stop(timeout=0.01)
            calls_before_restart = collector.calls
            starter = threading.Thread(target=engine.start)
            starter.start()
            starter.join(timeout=0.1)

            self.assertTrue(engine.is_running)
            self.assertFalse(starter.is_alive())
            self.assertEqual(calls_before_restart, collector.calls)
        finally:
            collector.release.set()
            engine.stop(timeout=1)
            if starter is not None:
                starter.join(timeout=1)

    def test_slow_subscriber_receives_latest_snapshot_without_blocking_engine(self):
        collector = MutableCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        engine = ObserverEngine(
            object(), collector=collector, poll_seconds=60, subscriber_queue_size=1
        )
        self.addCleanup(engine.stop)
        engine.start()
        subscriber = engine.subscribe()

        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00", connection="degraded"
        )
        engine.poll_once()

        self.assertEqual("degraded", subscriber.get(timeout=0.2)["connection"])

    def test_subscribe_seed_cannot_overwrite_a_newer_broadcast(self):
        collector = MutableCollector(FakeSnapshot("2026-09-11T10:00:00+00:00"))
        engine = CoordinatedOfferEngine(
            object(), collector=collector, poll_seconds=60, subscriber_queue_size=1
        )
        self.addCleanup(engine.stop)
        engine.start()
        collector.snapshot = FakeSnapshot(
            "2026-09-11T10:00:01+00:00", connection="degraded"
        )
        subscribers = []
        subscribe_thread = threading.Thread(
            target=lambda: subscribers.append(engine.subscribe())
        )
        subscribe_thread.start()
        self.assertTrue(engine.initial_offer_started.wait(timeout=0.5))

        poll_thread = threading.Thread(target=engine.poll_once)
        poll_thread.start()
        engine.changed_offer_completed.wait(timeout=0.1)
        engine.release_initial_offer.set()
        subscribe_thread.join(timeout=1)
        poll_thread.join(timeout=1)

        self.assertFalse(subscribe_thread.is_alive())
        self.assertFalse(poll_thread.is_alive())
        self.assertEqual("degraded", subscribers[0].get(timeout=0.2)["connection"])


if __name__ == "__main__":
    unittest.main()
