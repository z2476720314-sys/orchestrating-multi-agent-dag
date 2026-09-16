from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SCRIPT = SKILL_ROOT / "scripts" / "workflow.py"
START_SCRIPT = SKILL_ROOT / "scripts" / "start-workflow.ps1"


def run_workflow(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class WorkflowCliTests(unittest.TestCase):
    def test_task_progress_and_delivery_append_sanitized_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            common = ("--workspace", str(workspace), "--run-id", "run-1")

            task = run_workflow(
                "task", *common, "--task-id", "task-1", "--agent", "codex",
                "--title", "Observer details", "--objective", "Show live progress",
                "--phase", "planned", "--read-scope", "observer",
                "--write-scope", "observer/runtime.py", "--success", "focused tests pass",
                "--prohibition", "do not expose prompts",
            )
            progress = run_workflow(
                "progress", *common, "--task-id", "task-1", "--agent", "codex",
                "--phase", "testing", "--message", "Running focused tests",
                "--evidence", "tests/test_runtime.py",
            )
            delivery = run_workflow(
                "deliver", *common, "--task-id", "task-1", "--agent", "codex",
                "--status", "completed", "--summary", "Live details shipped",
                "--file", "observer/runtime.py", "--test", "pytest: 3 passed",
                "--artifact", "artifacts/proof.png", "--risk", "none",
            )

            for result in (task, progress, delivery):
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            ledger = workspace / ".agent-coordination" / "runtime" / "events.jsonl"
            events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["task", "progress", "delivery"], [event["kind"] for event in events])
            self.assertEqual("testing", events[1]["phase"])
            self.assertEqual(["observer/runtime.py"], events[2]["deliverables"]["files"])
            self.assertEqual(["pytest: 3 passed"], events[2]["deliverables"]["tests"])
            self.assertNotIn(str(workspace), ledger.read_text(encoding="utf-8"))

            task_result = json.loads(task.stdout)
            brief = Path(task_result["brief"])
            self.assertTrue(brief.is_file())
            brief_text = brief.read_text(encoding="utf-8")
            self.assertIn("observer/runtime.py", brief_text)
            self.assertIn("workflow.py progress", brief_text)
            self.assertIn("workflow.py deliver", brief_text)

            handoff = workspace / events[2]["deliverables"]["handoff"]
            self.assertTrue(handoff.is_file())
            handoff_text = handoff.read_text(encoding="utf-8")
            self.assertIn("Live details shipped", handoff_text)
            self.assertIn("pytest: 3 passed", handoff_text)

            hidden = run_workflow(
                "progress", *common, "--task-id", "task-1", "--agent", "codex",
                "--phase", "testing", "--message", "api_key=placeholder",
            )
            self.assertEqual(hidden.returncode, 0, hidden.stderr or hidden.stdout)
            last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual("[内容已隐藏]", last["detail"])

    def test_rejects_invalid_identifiers_and_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            invalid_id = run_workflow(
                "task", "--workspace", str(workspace), "--run-id", "bad id",
                "--task-id", "task", "--agent", "codex", "--title", "x",
                "--objective", "x", "--phase", "planned",
            )
            outside = run_workflow(
                "deliver", "--workspace", str(workspace), "--run-id", "run-1",
                "--task-id", "task", "--agent", "codex", "--status", "completed",
                "--summary", "x", "--handoff", "../outside.md",
            )

            self.assertNotEqual(invalid_id.returncode, 0)
            self.assertNotEqual(outside.returncode, 0)

    def test_start_workflow_refreshes_stale_observer_and_reuses_live_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            port = free_port()
            command = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(START_SCRIPT), "-Workspace", str(workspace),
                "-Port", str(port), "-NoBrowser",
            ]
            pid = None
            try:
                first = subprocess.run(command, check=False, capture_output=True, text=True)
                self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
                state_path = workspace / ".agent-coordination" / "runtime" / "observer.json"
                state = json.loads(state_path.read_text(encoding="utf-8-sig"))
                pid = int(state["pid"])
                self.assertTrue(state["url"].endswith(f":{port}/"))
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                    self.assertEqual(200, response.status)

                second = subprocess.run(command, check=False, capture_output=True, text=True)
                self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
                reused = json.loads(state_path.read_text(encoding="utf-8-sig"))
                self.assertEqual(pid, int(reused["pid"]))
                self.assertIn("reused: true", second.stdout.lower())
            finally:
                if pid is not None:
                    subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                        check=False,
                        capture_output=True,
                    )


    def test_shipped_template_stays_free_of_bytecode_residue(self) -> None:
        template = SKILL_ROOT / "assets" / "observer-template"
        for residue in template.rglob("__pycache__"):
            shutil.rmtree(residue, ignore_errors=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            result = run_workflow(
                "progress", "--workspace", str(workspace), "--run-id", "run-1",
                "--task-id", "task", "--agent", "codex",
                "--phase", "testing", "--message", "bytecode residue check",
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(tuple(template.rglob("__pycache__")), ())
        self.assertEqual(tuple(template.rglob("*.pyc")), ())


if __name__ == "__main__":
    unittest.main()
