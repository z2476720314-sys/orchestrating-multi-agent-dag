import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path

import run
from observer.model import Snapshot


class RecordingEngine:
    def __init__(self, config, *, poll_seconds):
        self.config = config
        self.poll_seconds = poll_seconds
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class RecordingServer:
    def __init__(self, config, engine):
        self.config = config
        self.engine = engine
        self.server_address = (config.host, config.port)
        self.served = False
        self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


class ObserverCliTests(unittest.TestCase):
    def assert_launcher_serves_health(self, powershell, *, environment=None):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        launcher = Path(__file__).resolve().parents[1] / "start-observer.ps1"
        process = subprocess.Popen(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-NoBrowser",
                "-Port",
                str(port),
            ],
            cwd=launcher.parent,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        output = ""
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output, _ = process.communicate()
                    self.fail(
                        "PowerShell launcher exited before health was ready:\n"
                        f"{output}"
                    )
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=0.25
                    ) as response:
                        self.assertEqual(200, response.status)
                        payload = json.load(response)
                        self.assertIn("status", payload)
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            else:
                self.fail("observer health endpoint did not become ready")
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    process.wait(timeout=10)
            if process.stdout is not None and not process.stdout.closed:
                output = process.stdout.read()
                process.stdout.close()

        self.assertIsNotNone(process.returncode)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.5)

    def test_defaults_use_workspace_root_loopback_and_port_8767(self):
        args = run.parse_args([])

        self.assertEqual(Path(__file__).resolve().parents[3], args.workspace)
        self.assertEqual("127.0.0.1", args.host)
        self.assertEqual(8767, args.port)
        self.assertEqual(1.0, args.poll_seconds)
        self.assertFalse(args.once)

    def test_explicit_port_is_passed_to_loopback_server(self):
        recorded = {}

        def server_factory(config, engine):
            server = RecordingServer(config, engine)
            recorded["server"] = server
            return server

        stdout = io.StringIO()
        exit_code = run.main(
            ["--port", "18767"],
            engine_factory=RecordingEngine,
            server_factory=server_factory,
            stdout=stdout,
        )

        server = recorded["server"]
        self.assertEqual(0, exit_code)
        self.assertEqual(("127.0.0.1", 18767), server.server_address)
        self.assertTrue(server.engine.started)
        self.assertTrue(server.served)
        self.assertTrue(server.closed)
        self.assertTrue(server.engine.stopped)
        self.assertIn("http://127.0.0.1:18767/", stdout.getvalue())

    def test_engine_stops_when_server_close_raises(self):
        recorded = {}

        class FailingCloseServer(RecordingServer):
            def server_close(self):
                self.closed = True
                raise RuntimeError("close failed")

        def server_factory(config, engine):
            server = FailingCloseServer(config, engine)
            recorded["server"] = server
            return server

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            run.main(
                [],
                engine_factory=RecordingEngine,
                server_factory=server_factory,
                stdout=io.StringIO(),
            )

        server = recorded["server"]
        self.assertTrue(server.closed)
        self.assertTrue(server.engine.stopped)

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_powershell_launcher_skips_bad_python_and_serves_health(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            bad_dir = temporary_path / "bad"
            good_dir = temporary_path / "good"
            bad_dir.mkdir()
            good_dir.mkdir()
            (bad_dir / "python.cmd").write_text(
                "@exit /b 19\n", encoding="ascii"
            )
            (good_dir / "python.cmd").write_text(
                f'@"{sys.executable}" %*\n', encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join(
                [str(bad_dir), str(good_dir), environment["PATH"]]
            )
            self.assert_launcher_serves_health(
                powershell, environment=environment
            )

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell is Windows-only")
    def test_windows_powershell_51_launcher_serves_health(self):
        windows_powershell = (
            Path(os.environ["SystemRoot"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not windows_powershell.is_file():
            self.skipTest("Windows PowerShell 5.1 is unavailable")

        self.assert_launcher_serves_health(str(windows_powershell))

    def test_non_loopback_host_is_rejected_by_argument_parser(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            run.parse_args(["--host", "0.0.0.0"])

        self.assertEqual(2, raised.exception.code)

    def test_once_prints_one_json_snapshot_without_starting_server(self):
        workspace = Path(__file__).resolve().parents[1]
        expected = Snapshot(
            generated_at="2026-09-11T12:00:00+00:00",
            workspace=str(workspace),
        )
        seen = {}

        def collector(config):
            seen["config"] = config
            return expected

        def unexpected_server_factory(_config, _engine):
            self.fail("--once must not create an HTTP server")

        stdout = io.StringIO()
        exit_code = run.main(
            ["--workspace", str(workspace), "--once"],
            collector=collector,
            server_factory=unexpected_server_factory,
            stdout=stdout,
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(workspace.resolve(), seen["config"].workspace)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("1", payload["schema_version"])
        self.assertEqual(str(workspace), payload["workspace"])
        self.assertEqual([], payload["agents"])


if __name__ == "__main__":
    unittest.main()
