import http.client
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

from observer.server import create_server

SNAPSHOT = {
    "schema_version": "1",
    "generated_at": "2026-09-11T10:00:00+00:00",
    "workspace": r"C:\workspace",
    "connection": "live",
    "agents": [],
    "tasks": [],
    "events": [],
    "source_health": [],
}


class FakeEngine:
    def __init__(self):
        self.subscribers: set[Queue[dict[str, object]]] = set()

    def snapshot_dict(self) -> dict[str, object]:
        return dict(SNAPSHOT)

    def subscribe(self) -> Queue[dict[str, object]]:
        subscriber: Queue[dict[str, object]] = Queue()
        subscriber.put(self.snapshot_dict())
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, object]]) -> None:
        self.subscribers.discard(subscriber)


class RunningServer:
    def __init__(self, static_dir: Path, *, heartbeat_seconds: float = 0.05):
        config = SimpleNamespace(
            host="127.0.0.1",
            port=0,
            static_dir=static_dir,
            sse_heartbeat_seconds=heartbeat_seconds,
        )
        self.engine = FakeEngine()
        self.server = create_server(config, self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)


class ObserverServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.static_dir = Path(self.temp.name)
        (self.static_dir / "index.html").write_text("<main>observer</main>", encoding="utf-8")
        (self.static_dir / "app.js").write_text("console.log('observer')", encoding="utf-8")
        (self.static_dir / "app.css").write_text("body{}", encoding="utf-8")

    def request(self, address: tuple[str, int], path: str):
        connection = http.client.HTTPConnection(*address, timeout=1)
        self.addCleanup(connection.close)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        return response, body

    def test_healthz_and_api_health_return_utf8_json(self):
        with RunningServer(self.static_dir) as running:
            for path in ("/healthz", "/api/health"):
                response, body = self.request(running.address, path)
                self.assertEqual(200, response.status)
                self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type"))
                self.assertEqual("no-store", response.getheader("Cache-Control"))
                self.assertEqual("ok", json.loads(body)["status"])

    def test_snapshot_returns_schema_and_disables_cache(self):
        with RunningServer(self.static_dir) as running:
            response, body = self.request(running.address, "/api/snapshot")

        self.assertEqual(200, response.status)
        self.assertEqual("1", json.loads(body)["schema_version"])
        self.assertEqual("no-store", response.getheader("Cache-Control"))

    def test_sse_starts_with_snapshot_event_and_heartbeat(self):
        with RunningServer(self.static_dir) as running:
            connection = http.client.HTTPConnection(*running.address, timeout=1)
            connection.request("GET", "/api/events")
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual("text/event-stream; charset=utf-8", response.getheader("Content-Type"))
            self.assertEqual("no-cache", response.getheader("Cache-Control"))

            initial = response.readline() + response.readline() + response.readline()
            heartbeat = response.readline() + response.readline() + response.readline()
            response.close()
            connection.close()

        self.assertIn(b"event: snapshot\n", initial)
        self.assertIn(b'"schema_version":"1"', initial)
        self.assertIn(b"event: heartbeat\n", heartbeat)

    def test_sse_changed_snapshot_uses_changed_event(self):
        with RunningServer(self.static_dir, heartbeat_seconds=1) as running:
            connection = http.client.HTTPConnection(*running.address, timeout=1)
            connection.request("GET", "/api/events")
            response = connection.getresponse()
            response.readline()
            response.readline()
            response.readline()
            subscriber = next(iter(running.engine.subscribers))
            changed = dict(SNAPSHOT, connection="degraded")
            subscriber.put(changed)

            event = response.readline() + response.readline() + response.readline()
            response.close()
            connection.close()

        self.assertIn(b"event: changed\n", event)
        self.assertIn(b'"connection":"degraded"', event)

    def test_root_and_allowlisted_static_assets_are_served(self):
        with RunningServer(self.static_dir) as running:
            root_response, root_body = self.request(running.address, "/")
            js_response, js_body = self.request(running.address, "/static/app.js")
            css_response, css_body = self.request(running.address, "/static/app.css")

        self.assertEqual(200, root_response.status)
        self.assertEqual(b"<main>observer</main>", root_body)
        self.assertEqual("text/html; charset=utf-8", root_response.getheader("Content-Type"))
        self.assertEqual(200, js_response.status)
        self.assertEqual(b"console.log('observer')", js_body)
        self.assertEqual("application/javascript; charset=utf-8", js_response.getheader("Content-Type"))
        self.assertEqual(200, css_response.status)
        self.assertEqual(b"body{}", css_body)
        self.assertEqual("text/css; charset=utf-8", css_response.getheader("Content-Type"))

    def test_static_traversal_and_unlisted_names_are_rejected(self):
        (self.static_dir.parent / "secret.txt").write_text("secret", encoding="utf-8")
        with RunningServer(self.static_dir) as running:
            for path in (
                "/static/../secret.txt",
                "/static/%2e%2e/secret.txt",
                "/static/secret.txt",
                "/static/nested/app.js",
            ):
                response, body = self.request(running.address, path)
                self.assertIn(response.status, (403, 404))
                self.assertNotIn(b"secret", body)

    def test_non_loopback_binding_is_rejected(self):
        config = SimpleNamespace(host="0.0.0.0", port=0, static_dir=self.static_dir)
        with self.assertRaises(ValueError):
            create_server(config, FakeEngine())

    def test_client_disconnect_removes_sse_subscription(self):
        stderr = io.StringIO()
        with (
            redirect_stderr(stderr),
            RunningServer(self.static_dir, heartbeat_seconds=0.01) as running,
        ):
            connection = http.client.HTTPConnection(*running.address, timeout=1)
            connection.request("GET", "/api/events")
            response = connection.getresponse()
            response.readline()
            response.readline()
            response.readline()
            response.close()
            connection.close()

            for _ in range(100):
                if not running.engine.subscribers:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(set(), running.engine.subscribers)
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
