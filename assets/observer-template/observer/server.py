"""Loopback-only HTTP and server-sent events transport."""

from __future__ import annotations

import json
import mimetypes
import queue
import select
import socket
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
STATIC_FILES = frozenset({"app.css", "app.js"})
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class EngineProtocol(Protocol):
    def snapshot_dict(self) -> dict[str, object]: ...

    def subscribe(self) -> queue.Queue[dict[str, object]]: ...

    def unsubscribe(self, subscriber: queue.Queue[dict[str, object]]) -> None: ...


def _config_value(config: object, name: str, default: Any) -> Any:
    direct = getattr(config, name, None)
    if direct is not None:
        return direct
    options = getattr(config, "source_options", None)
    if isinstance(options, Mapping) and options.get(name) is not None:
        return options[name]
    return default


class ObserverHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        engine: EngineProtocol,
        static_dir: Path,
        heartbeat_seconds: float,
    ) -> None:
        self.engine = engine
        self.static_dir = static_dir.resolve()
        self.heartbeat_seconds = heartbeat_seconds
        super().__init__(server_address, handler_class)


class ObserverRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ObserverHTTP/1"

    @property
    def observer_server(self) -> ObserverHTTPServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in {"/healthz", "/api/health"}:
            self._send_health()
        elif path == "/api/snapshot":
            self._send_snapshot()
        elif path == "/api/events":
            self._send_events()
        elif path == "/":
            self._send_static("index.html")
        elif path.startswith("/static/"):
            self._send_static(path.removeprefix("/static/"))
        else:
            self._send_not_found()

    def _send_health(self) -> None:
        payload: dict[str, object] = {"status": "ok"}
        try:
            snapshot = self.observer_server.engine.snapshot_dict()
        except RuntimeError:
            payload["connection"] = "degraded"
        else:
            payload["connection"] = snapshot.get("connection", "unknown")
        self._send_json(payload)

    def _send_snapshot(self) -> None:
        try:
            snapshot = self.observer_server.engine.snapshot_dict()
        except RuntimeError:
            self._send_json(
                {"error": "snapshot unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        self._send_json(snapshot)

    def _send_events(self) -> None:
        subscriber = self.observer_server.engine.subscribe()
        try:
            try:
                initial = subscriber.get_nowait()
            except queue.Empty:
                initial = self.observer_server.engine.snapshot_dict()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self._write_event("snapshot", initial)

            while not self._client_disconnected():
                try:
                    snapshot = subscriber.get(
                        timeout=self.observer_server.heartbeat_seconds
                    )
                except queue.Empty:
                    self._write_event("heartbeat", {})
                else:
                    self._write_event("changed", snapshot)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            # A browser tab can disappear at any point in a streaming response.
            pass
        finally:
            # SSE owns the connection until disconnect. Prevent the base HTTP
            # loop from attempting to read a second request from a closed peer.
            self.close_connection = True
            self.observer_server.engine.unsubscribe(subscriber)

    def _client_disconnected(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _write_event(self, event: str, data: object) -> None:
        serialized = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"), default=str
        )
        self.wfile.write(f"event: {event}\ndata: {serialized}\n\n".encode())
        self.wfile.flush()

    def _send_json(
        self, payload: object, *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
        self._send_bytes(
            status,
            body,
            content_type="application/json; charset=utf-8",
            cache_control="no-store",
        )

    def _send_static(self, name: str) -> None:
        allowed = name == "index.html" or name in STATIC_FILES
        if not allowed or Path(name).name != name:
            self._send_not_found()
            return

        root = self.observer_server.static_dir
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send_not_found()
            return
        if not target.is_file():
            self._send_not_found()
            return

        content_type = CONTENT_TYPES.get(target.suffix.lower())
        if content_type is None:
            guessed, _ = mimetypes.guess_type(target.name)
            content_type = guessed or "application/octet-stream"
        self._send_bytes(
            HTTPStatus.OK,
            target.read_bytes(),
            content_type=content_type,
            cache_control="no-cache",
        )

    def _send_not_found(self) -> None:
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        # The embedding CLI owns normal logging. In particular, routine SSE
        # disconnects must not flood stderr with per-client tracebacks.
        return


def create_server(config: object, engine: EngineProtocol) -> ObserverHTTPServer:
    """Create a loopback-only observer server without starting its loop."""
    host = str(_config_value(config, "host", LOOPBACK_HOST))
    if host != LOOPBACK_HOST:
        raise ValueError(f"observer host must be {LOOPBACK_HOST}")
    port = int(_config_value(config, "port", DEFAULT_PORT))
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")

    default_static = Path(__file__).resolve().parent.parent / "static"
    static_dir = Path(_config_value(config, "static_dir", default_static))
    heartbeat_seconds = float(
        _config_value(config, "sse_heartbeat_seconds", 15.0)
    )
    if heartbeat_seconds <= 0:
        raise ValueError("sse_heartbeat_seconds must be positive")

    return ObserverHTTPServer(
        (host, port),
        ObserverRequestHandler,
        engine=engine,
        static_dir=static_dir,
        heartbeat_seconds=heartbeat_seconds,
    )
