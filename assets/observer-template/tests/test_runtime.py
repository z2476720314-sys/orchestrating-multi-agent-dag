from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observer.adapters.runtime import RuntimeSource
from observer.model import ObserverConfig


class RuntimeAdapterTests(unittest.TestCase):
    def test_reduces_task_progress_and_structured_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            runtime = workspace / ".agent-coordination" / "runtime"
            runtime.mkdir(parents=True)
            events = [
                {
                    "schema_version": "1", "id": "evt-1", "run_id": "run-1",
                    "task_id": "task-1", "agent": "codex", "kind": "task",
                    "status": "planned", "phase": "planned", "title": "Live observer",
                    "detail": "Show detailed progress", "timestamp": "2026-09-14T01:00:00Z",
                    "evidence": [], "deliverables": {},
                },
                {
                    "schema_version": "1", "id": "evt-2", "run_id": "run-1",
                    "task_id": "task-1", "agent": "codex", "kind": "progress",
                    "status": "running", "phase": "testing", "title": "Progress",
                    "detail": "Running focused tests", "timestamp": "2026-09-14T01:01:00Z",
                    "evidence": ["tests/test_runtime.py"], "deliverables": {},
                },
                {
                    "schema_version": "1", "id": "evt-3", "run_id": "run-1",
                    "task_id": "task-1", "agent": "codex", "kind": "delivery",
                    "status": "completed", "phase": "delivered", "title": "Delivery",
                    "detail": "Detailed progress shipped", "timestamp": "2026-09-14T01:02:00Z",
                    "evidence": ["handoffs/task-1.md"],
                    "deliverables": {
                        "files": ["observer/runtime.py"], "tests": ["pytest: 3 passed"],
                        "artifacts": ["artifacts/proof.png"], "handoff": "handoffs/task-1.md",
                        "risks": ["none"], "unresolved": [],
                    },
                },
            ]
            (runtime / "events.jsonl").write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )

            result = RuntimeSource(ObserverConfig(workspace=workspace)).collect()

            self.assertEqual("healthy", result.health.status)
            self.assertEqual(1, len(result.tasks))
            task = result.tasks[0]
            self.assertEqual("completed", task.status)
            self.assertEqual("delivered", task.phase)
            self.assertEqual("Detailed progress shipped", task.last_progress)
            self.assertEqual(3, len(result.events))
            delivery = result.events[-1]
            self.assertEqual("delivery", delivery.kind)
            self.assertEqual(("observer/runtime.py",), delivery.deliverables)
            self.assertEqual(("pytest: 3 passed",), delivery.tests)
            self.assertEqual("handoffs/task-1.md", delivery.handoff)

    def test_invalid_runtime_ledger_degrades_without_echoing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            runtime = workspace / ".agent-coordination" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "events.jsonl").write_text("not-json\n", encoding="utf-8")

            result = RuntimeSource(ObserverConfig(workspace=workspace)).collect()

            self.assertEqual("degraded", result.health.status)
            self.assertEqual((), result.events)
            self.assertNotIn("not-json", json.dumps(result.to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
