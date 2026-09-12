import unittest
from pathlib import Path
from typing import Any, cast

from observer.model import (
    AgentState,
    ObserverConfig,
    Snapshot,
    SourceHealth,
    TaskState,
    TraceEvent,
)


class ModelTests(unittest.TestCase):
    def test_agent_defaults_to_honest_unknown_state(self) -> None:
        agent = AgentState(id="dsh:x", name="DSH", kind="dsh")

        self.assertEqual("unknown", agent.status)
        self.assertEqual("暂无证据", agent.status_evidence)

    def test_snapshot_serializes_nested_immutable_records(self) -> None:
        snapshot = Snapshot(
            generated_at="2026-09-11T12:00:00+00:00",
            workspace=r"C:\workspace\project",
            connection="degraded",
            agents=(AgentState(id="codex:a", name="Codex", kind="codex"),),
            tasks=(TaskState(id="task:a", title="Adapter tests"),),
            events=(
                TraceEvent(
                    id="event:a",
                    source="codex",
                    agent_id="codex:a",
                    kind="lifecycle",
                    title="Task started",
                ),
            ),
            source_health=(SourceHealth(source="codex", status="degraded"),),
        )

        result = snapshot.to_dict()
        agents = cast(list[dict[str, Any]], result["agents"])
        events = cast(list[dict[str, Any]], result["events"])

        self.assertEqual("1", result["schema_version"])
        self.assertEqual("degraded", result["connection"])
        self.assertEqual("unknown", agents[0]["status"])
        self.assertEqual("暂无证据", events[0]["detail"])
        self.assertIsInstance(agents, list)

    def test_records_are_immutable(self) -> None:
        agent = AgentState(id="codex:a", name="Codex", kind="codex")

        with self.assertRaises(AttributeError):
            agent.status = "running"  # type: ignore[misc]

    def test_observer_config_normalizes_workspace_without_reading_it(self) -> None:
        config = ObserverConfig(workspace=Path(r"C:\workspace\project"))

        self.assertEqual(Path(r"C:\workspace\project"), config.workspace)
        self.assertEqual(30.0, config.stale_after_seconds)
        self.assertEqual(12, config.max_codex_sessions)
        self.assertEqual(12, config.max_dsh_sessions)
        self.assertEqual(80, config.max_events_per_source)
        self.assertEqual(65_536, config.codex_head_bytes)
        self.assertEqual(1_048_576, config.codex_tail_bytes)
        self.assertIsNone(config.workbuddy_jobs_dir)


if __name__ == "__main__":
    unittest.main()
