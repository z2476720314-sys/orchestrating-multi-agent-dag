import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from observer.adapters import (
    CodexSource,
    CoordinationSource,
    DshSource,
    WorkBuddySource,
    collect_snapshot,
)
from observer.model import ObserverConfig

FIXTURES = Path(__file__).with_name("fixtures")
WORKSPACE = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _copy_with_workspace(self, fixture: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (FIXTURES / fixture).read_text(encoding="utf-8")
        escaped_workspace = str(WORKSPACE).replace("\\", "\\\\")
        target.write_text(content.replace("__WORKSPACE__", escaped_workspace), encoding="utf-8")
        return target

    def _write_codex_rollout(
        self, target: Path, session_id: str, records: list[dict[str, object]]
    ) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(WORKSPACE)},
        }
        lines = [json.dumps(meta), *(json.dumps(record) for record in records)]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def test_coordination_markdown_maps_declared_agents_and_tasks(self) -> None:
        source = CoordinationSource(
            ObserverConfig(
                workspace=WORKSPACE,
                coordination_status_path=FIXTURES / "coordination-status.md",
                handoffs_dir=self.root / "handoffs",
            )
        )

        result = source.collect()

        self.assertEqual("healthy", result.health.status)
        self.assertEqual(
            ["coordination:codex", "coordination:dsh", "coordination:workbuddy"],
            [agent.id for agent in result.agents],
        )
        self.assertEqual("running", result.agents[0].status)
        self.assertEqual("unknown", result.agents[1].status)
        self.assertEqual("waiting", result.agents[2].status)
        self.assertEqual("completed", result.tasks[0].status)
        self.assertEqual("running", result.tasks[1].status)
        self.assertEqual("observer-model", result.tasks[0].title)
        self.assertEqual("unsafe-title", result.tasks[1].title)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("Prompt Runner", serialized)
        self.assertNotIn("developer instructions", serialized)

    def test_coordination_skips_invalid_task_ids_and_unsafe_handoff_stems(self) -> None:
        status = self.root / "status.md"
        status.write_text(
            "| Agent | 当前状态 |\n"
            "|---|---|\n"
            "| Codex | running |\n"
            "## 当前任务\n"
            "| 任务 ID | 负责人 | 状态 | 范围 | 产出 |\n"
            "|---|---|---|---|---|\n"
            "| unsafe id | Codex | DONE | observer | prompt: private |\n",
            encoding="utf-8",
        )
        handoffs = self.root / "handoffs"
        handoffs.mkdir()
        (handoffs / "safe-task.md").write_text("prompt: private", encoding="utf-8")
        (handoffs / "bad task!.md").write_text("prompt: private", encoding="utf-8")

        result = CoordinationSource(
            ObserverConfig(
                workspace=self.root,
                coordination_status_path=status,
                handoffs_dir=handoffs,
            )
        ).collect()

        self.assertEqual(["safe-task"], [task.id for task in result.tasks])
        self.assertEqual("safe-task", result.tasks[0].title)
        self.assertNotIn("private", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_coordination_normalizes_declared_workflow_statuses(self) -> None:
        status = self.root / "status.md"
        rows = (
            ("planned-task", "PLANNED", "planned"),
            ("running-task", "RUNNING", "running"),
            ("waiting-task", "WAITING", "waiting"),
            ("blocked-task", "BLOCKED", "blocked"),
            ("failed-task", "FAILED", "failed"),
            ("done-task", "DONE", "completed"),
        )
        status.write_text(
            "## 当前任务\n"
            "| Task ID | Owner | Status | Read | Write |\n"
            "|---|---|---|---|---|\n"
            + "".join(
                f"| {task_id} | Codex | {declared} | src | out |\n"
                for task_id, declared, _ in rows
            ),
            encoding="utf-8",
        )

        result = CoordinationSource(
            ObserverConfig(
                workspace=self.root,
                coordination_status_path=status,
                handoffs_dir=self.root / "handoffs",
            )
        ).collect()

        self.assertEqual(
            {task_id: normalized for task_id, _, normalized in rows},
            {task.id: task.status for task in result.tasks},
        )

    def test_coordination_strips_known_owner_suffix_from_handoff_task_id(self) -> None:
        status = self.root / "status.md"
        status.write_text(
            "## 当前任务\n"
            "| Task ID | Owner | Status | Read | Write |\n"
            "|---|---|---|---|---|\n"
            "| shared-task | Codex | RUNNING | src | out |\n",
            encoding="utf-8",
        )
        handoffs = self.root / "handoffs"
        handoffs.mkdir()
        (handoffs / "shared-task-dsh.md").write_text("evidence", encoding="utf-8")
        (handoffs / "orphan-workbuddy.md").write_text("evidence", encoding="utf-8")

        result = CoordinationSource(
            ObserverConfig(
                workspace=self.root,
                coordination_status_path=status,
                handoffs_dir=handoffs,
            )
        ).collect()

        self.assertEqual(["shared-task", "orphan"], [task.id for task in result.tasks])
        self.assertEqual("coordination:workbuddy", result.tasks[1].owner_id)

    def test_coordination_degrades_when_handoffs_cannot_be_enumerated(self) -> None:
        status = self.root / "status.md"
        status.write_text(
            "| Agent | 当前状态 |\n"
            "|---|---|\n"
            "| Codex | running |\n"
            "## 当前任务\n"
            "| 任务 ID | 负责人 | 状态 | 范围 | 产出 |\n"
            "|---|---|---|---|---|\n"
            "| safe-task | Codex | RUNNING | observer | safe-task |\n",
            encoding="utf-8",
        )
        handoffs = self.root / "handoffs"
        handoffs.mkdir()

        with mock.patch.object(Path, "glob", side_effect=OSError("unreadable")):
            result = CoordinationSource(
                ObserverConfig(
                    workspace=self.root,
                    coordination_status_path=status,
                    handoffs_dir=handoffs,
                )
            ).collect()

        self.assertEqual("degraded", result.health.status)
        self.assertIn("handoff", result.health.detail.casefold())
        self.assertEqual(["safe-task"], [task.id for task in result.tasks])

    def test_codex_filters_workspace_and_uses_lifecycle_only(self) -> None:
        self._copy_with_workspace("codex-rollout.jsonl", self.root / "rollout-match.jsonl")
        outside = (FIXTURES / "codex-rollout.jsonl").read_text(encoding="utf-8")
        (self.root / "rollout-outside.jsonl").write_text(
            outside.replace("__WORKSPACE__", r"C:\outside"), encoding="utf-8"
        )
        source = CodexSource(
            ObserverConfig(workspace=WORKSPACE, codex_sessions_root=self.root)
        )

        result = source.collect()

        self.assertEqual(1, len(result.agents))
        self.assertEqual("completed", result.agents[0].status)
        self.assertEqual("root-session", result.agents[0].parent_id)
        self.assertEqual(
            ["completed", "started", "completed", "completed"],
            [event.status for event in result.events],
        )
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("raw private prompt", serialized)
        self.assertNotIn("Bearer secret", serialized)
        self.assertNotIn("private result", serialized)

    def test_codex_trace_links_session_turn_and_tool_events(self) -> None:
        self._copy_with_workspace(
            "codex-rollout.jsonl", self.root / "rollout-match.jsonl"
        )

        result = CodexSource(
            ObserverConfig(workspace=WORKSPACE, codex_sessions_root=self.root)
        ).collect()

        by_id = {event.id: event for event in result.events}
        session_id = "codex:codex-session:session"
        started_id = "codex:codex-session:turn-1:task_started"
        tool_id = "codex:codex-session:call-1"
        completed_id = "codex:codex-session:turn-1:task_complete"
        self.assertIn(session_id, by_id)
        self.assertEqual(session_id, by_id[started_id].parent_id)
        self.assertEqual(started_id, by_id[tool_id].parent_id)
        self.assertEqual(started_id, by_id[completed_id].parent_id)

    def test_codex_reserves_a_recent_slot_for_a_missing_parent_session(self) -> None:
        parent = self._write_codex_rollout(
            self.root / "rollout-2026-09-11-parent-session.jsonl",
            "parent-session",
            [],
        )
        child_old = self._write_codex_rollout(
            self.root / "rollout-child-old.jsonl",
            "child-old",
            [],
        )
        child_new = self.root / "rollout-child-new.jsonl"
        child_new.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "child-new",
                        "cwd": str(WORKSPACE),
                        "parent_thread_id": "parent-session",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(parent, (1000.0, 1000.0))
        os.utime(child_old, (1001.0, 1001.0))
        os.utime(child_new, (1002.0, 1002.0))

        result = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                max_codex_sessions=2,
            )
        ).collect()

        self.assertEqual(
            {"codex:parent-session", "codex:child-new"},
            {agent.id for agent in result.agents},
        )
        session_events = {event.id: event for event in result.events}
        self.assertEqual(
            "codex:parent-session:session",
            session_events["codex:child-new:session"].parent_id,
        )

    def test_codex_reads_large_rollout_with_bounded_head_and_tail(self) -> None:
        path = self.root / "rollout-large.jsonl"
        meta = {
            "type": "session_meta",
            "payload": {"id": "large-session", "cwd": str(WORKSPACE)},
        }
        filler = {
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": "保密内容"},
        }
        started = {
            "type": "event_msg",
            "timestamp": "2026-09-11T12:00:00Z",
            "payload": {"type": "task_started", "turn_id": "turn-large"},
        }
        path.write_bytes(
            (json.dumps(meta) + "\n").encode()
            + ((json.dumps(filler, ensure_ascii=False) + "\n").encode() * 100)
            + (json.dumps(started) + "\n").encode()
        )
        source = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                max_file_bytes=256,
                codex_head_bytes=256,
                codex_tail_bytes=256,
            )
        )

        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            result = source.collect()

        self.assertEqual(["codex:large-session"], [agent.id for agent in result.agents])
        self.assertEqual("running", result.agents[0].status)
        self.assertEqual("healthy", result.health.status)

    def test_codex_limits_default_history_to_most_recent_sessions(self) -> None:
        for index in range(3):
            path = self._write_codex_rollout(
                self.root / f"rollout-{index}.jsonl", f"session-{index}", []
            )
            os.utime(path, (1000.0 + index, 1000.0 + index))

        result = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                max_codex_sessions=2,
            )
        ).collect()

        self.assertEqual(
            {"codex:session-1", "codex:session-2"},
            {agent.id for agent in result.agents},
        )

    def test_codex_maps_real_item_types_and_preserves_unknown_status(self) -> None:
        items = (
            {"type": "CommandExecution", "id": "cmd-1", "status": "completed"},
            {"type": "McpToolCall", "id": "mcp-1", "tool": "read_resource", "status": "failed"},
            {"type": "CollabAgentToolCall", "id": "agent-1", "tool": "spawn_agent", "status": "mystery"},
            {"type": "FileChange", "id": "file-1"},
        )
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "item_completed", "turn_id": "turn-1", "item": item},
            }
            for item in items
        ]
        self._write_codex_rollout(self.root / "rollout-items.jsonl", "items-session", records)

        result = CodexSource(
            ObserverConfig(workspace=WORKSPACE, codex_sessions_root=self.root)
        ).collect()

        tool_events = [event for event in result.events if event.kind == "tool"]
        self.assertEqual(
            ["CommandExecution", "read_resource", "spawn_agent", "FileChange"],
            [event.tool_name for event in tool_events],
        )
        self.assertEqual(
            ["completed", "failed", "unknown", "unknown"],
            [event.status for event in tool_events],
        )

    def test_codex_uses_fixed_name_and_hides_invalid_model_and_tool_tokens(self) -> None:
        meta = {
            "type": "session_meta",
            "payload": {
                "id": "safe-session",
                "cwd": "\\\\?\\" + str(WORKSPACE),
                "agent_path": ["developer instructions: private"],
                "model": "model with prompt",
            },
        }
        item = {
            "type": "event_msg",
            "timestamp": "prompt: private",
            "payload": {
                "type": "item_completed",
                "item": {"type": "McpToolCall", "id": "call-1", "tool": "tool(args)"},
            },
        }
        (self.root / "rollout-safe.jsonl").write_text(
            json.dumps(meta) + "\n" + json.dumps(item) + "\n", encoding="utf-8"
        )

        result = CodexSource(
            ObserverConfig(workspace=WORKSPACE, codex_sessions_root=self.root)
        ).collect()

        self.assertEqual("Codex", result.agents[0].name)
        self.assertEqual("[内容已隐藏]", result.agents[0].model)
        tool_event = next(event for event in result.events if event.kind == "tool")
        self.assertEqual("[内容已隐藏]", tool_event.tool_name)
        self.assertIsNone(tool_event.ended_at)
        self.assertNotIn("private", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_codex_completion_for_another_turn_does_not_close_active_turn(self) -> None:
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-active"},
            },
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-old"},
            },
        ]
        self._write_codex_rollout(self.root / "rollout-turns.jsonl", "turn-session", records)

        result = CodexSource(
            ObserverConfig(workspace=WORKSPACE, codex_sessions_root=self.root)
        ).collect()

        self.assertEqual("running", result.agents[0].status)

    def test_codex_tail_discards_a_split_utf8_prefix_before_decoding(self) -> None:
        path = self.root / "rollout-utf8.jsonl"
        meta = json.dumps(
            {"type": "session_meta", "payload": {"id": "utf8-session", "cwd": str(WORKSPACE)}}
        ).encode()
        started = (
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-utf8"},
                }
            )
            + "\n"
        ).encode()
        emoji_line = ('{"type":"response_item","payload":"' + ("界" * 80) + '"}\n').encode()
        split_at = emoji_line.index("界".encode()) + 1
        tail_bytes = len(emoji_line) - split_at + len(started)
        path.write_bytes(meta + b"\n" + (b'{"type":"padding"}\n' * 100) + emoji_line + started)

        result = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                codex_head_bytes=len(meta) + 1,
                codex_tail_bytes=tail_bytes,
            )
        ).collect()

        self.assertEqual("healthy", result.health.status)
        self.assertEqual("running", result.agents[0].status)

    def test_codex_tail_keeps_first_line_when_seek_starts_on_line_boundary(self) -> None:
        path = self.root / "rollout-boundary.jsonl"
        meta = (
            json.dumps(
                {"type": "session_meta", "payload": {"id": "boundary-session", "cwd": str(WORKSPACE)}}
            )
            + "\n"
        ).encode()
        event = (
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-boundary"},
                }
            )
            + "\n"
        ).encode()
        path.write_bytes(meta + (b'{"type":"padding"}\n' * 100) + event)

        result = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                codex_head_bytes=len(meta),
                codex_tail_bytes=len(event),
            )
        ).collect()

        self.assertEqual("running", result.agents[0].status)

    def test_codex_returns_only_latest_events_in_stable_order(self) -> None:
        records = [
            {
                "type": "event_msg",
                "timestamp": f"2026-09-11T12:{index:02d}:00Z",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": f"cmd-{index}",
                        "status": "completed",
                    },
                },
            }
            for index in range(5)
        ]
        self._write_codex_rollout(self.root / "rollout-events.jsonl", "event-session", records)

        result = CodexSource(
            ObserverConfig(
                workspace=WORKSPACE,
                codex_sessions_root=self.root,
                max_events_per_source=3,
            )
        ).collect()

        self.assertEqual(
            [
                "codex:event-session:session",
                "codex:event-session:cmd-3",
                "codex:event-session:cmd-4",
            ],
            [event.id for event in result.events],
        )

    def test_dsh_maps_fresh_projection_but_stale_open_turn_is_unknown(self) -> None:
        session_path = self._copy_with_workspace(
            "dsh-session.json", self.root / "sessions" / "dsh-session.json"
        )
        os.utime(session_path, (1000.0, 1000.0))
        fresh = DshSource(
            ObserverConfig(
                workspace=WORKSPACE,
                dsh_sessions_dir=session_path.parent,
                clock=lambda: 1002.0,
                stale_after_seconds=5.0,
            )
        ).collect()

        self.assertEqual("running", fresh.agents[0].status)
        self.assertEqual("DSH", fresh.agents[0].name)
        self.assertEqual("workbuddy/deepseek-v4.1-flash", fresh.agents[0].model)
        self.assertEqual("max", fresh.agents[0].effort)
        serialized = json.dumps(fresh.to_dict(), ensure_ascii=False)
        self.assertNotIn("titleInput", serialized)
        self.assertNotIn("turnOutline", serialized)
        self.assertNotIn("private", serialized)

        stale = DshSource(
            ObserverConfig(
                workspace=WORKSPACE,
                dsh_sessions_dir=session_path.parent,
                clock=lambda: 1010.0,
                stale_after_seconds=5.0,
            )
        ).collect()

        self.assertEqual("unknown", stale.agents[0].status)
        self.assertEqual("证据已陈旧", stale.agents[0].status_evidence)

    def test_dsh_uses_safe_file_stem_when_real_identity_has_no_id(self) -> None:
        session = {
            "record": {
                "identity": {
                    "formatVersion": 3,
                    "cwd": "\\\\?\\" + str(WORKSPACE),
                },
                "rows": {
                    "title": {"val": "developer instructions: private"},
                    "turnBoundary": {"val": {"openTurnStartSeq": 7}},
                    "sessionStats": {"val": {"openStep": None, "pendingCalls": {}}},
                },
            }
        }
        sessions = self.root / "sessions"
        sessions.mkdir()
        (sessions / "d3e8b182-adde-4528-9f2a-0b8c81210f22.json").write_text(
            json.dumps(session), encoding="utf-8"
        )
        (sessions / "bad stem!.json").write_text(json.dumps(session), encoding="utf-8")

        result = DshSource(
            ObserverConfig(workspace=WORKSPACE, dsh_sessions_dir=sessions)
        ).collect()

        self.assertEqual(["dsh:d3e8b182-adde-4528-9f2a-0b8c81210f22"], [a.id for a in result.agents])
        self.assertEqual("DSH", result.agents[0].name)
        self.assertNotIn("private", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_dsh_limits_history_to_most_recent_sessions(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir()
        for index in range(3):
            payload = {
                "record": {
                    "identity": {"cwd": str(WORKSPACE)},
                    "rows": {"turnBoundary": {"val": {"openTurnStartSeq": index}}},
                }
            }
            path = sessions / f"dsh-{index}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.utime(path, (1000.0 + index, 1000.0 + index))

        result = DshSource(
            ObserverConfig(
                workspace=WORKSPACE,
                dsh_sessions_dir=sessions,
                max_dsh_sessions=2,
            )
        ).collect()

        self.assertEqual({"dsh:dsh-1", "dsh:dsh-2"}, {agent.id for agent in result.agents})

    def test_dsh_returns_only_latest_events_in_stable_order(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir()
        for index in range(3):
            payload = {
                "record": {
                    "identity": {"cwd": str(WORKSPACE)},
                    "rows": {"turnBoundary": {"val": {"openTurnStartSeq": index}}},
                }
            }
            path = sessions / f"event-{index}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.utime(path, (1000.0 + index, 1000.0 + index))

        result = DshSource(
            ObserverConfig(
                workspace=WORKSPACE,
                dsh_sessions_dir=sessions,
                max_events_per_source=2,
            )
        ).collect()

        self.assertEqual(
            ["dsh:event-1:projection", "dsh:event-2:projection"],
            [event.id for event in result.events],
        )

    def test_dsh_pending_call_dictionary_is_running(self) -> None:
        payload = {
            "record": {
                "identity": {"cwd": str(WORKSPACE)},
                "rows": {
                    "turnBoundary": {"val": {"openTurnStartSeq": None}},
                    "sessionStats": {
                        "val": {
                            "openStep": None,
                            "pendingCalls": {"call-safe": {"prompt": "private"}},
                        }
                    },
                },
            }
        }
        sessions = self.root / "sessions"
        sessions.mkdir()
        (sessions / "pending-session.json").write_text(json.dumps(payload), encoding="utf-8")

        result = DshSource(
            ObserverConfig(workspace=WORKSPACE, dsh_sessions_dir=sessions)
        ).collect()

        self.assertEqual("running", result.agents[0].status)
        self.assertNotIn("private", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_dsh_null_open_fields_need_terminal_evidence_for_completed(self) -> None:
        base = {
            "version": 1,
            "record": {
                "identity": {"cwd": str(WORKSPACE)},
                "rows": {},
            },
        }
        sessions = self.root / "sessions"
        sessions.mkdir()
        (sessions / "missing.json").write_text(json.dumps(base), encoding="utf-8")
        explicit = json.loads(json.dumps(base))
        explicit["record"]["rows"] = {
            "turnBoundary": {"val": {"openTurnStartSeq": None}},
            "sessionStats": {"val": {"openStep": None, "pendingCalls": []}},
        }
        (sessions / "null-only.json").write_text(json.dumps(explicit), encoding="utf-8")
        complete = json.loads(json.dumps(explicit))
        complete["record"]["rows"]["turnBoundary"]["val"]["lastTurn"] = 3
        (sessions / "complete.json").write_text(json.dumps(complete), encoding="utf-8")

        result = DshSource(
            ObserverConfig(workspace=WORKSPACE, dsh_sessions_dir=sessions)
        ).collect()
        states = {agent.id: agent.status for agent in result.agents}

        self.assertEqual("unknown", states["dsh:missing"])
        self.assertEqual("unknown", states["dsh:null-only"])
        self.assertEqual("completed", states["dsh:complete"])

    def test_workbuddy_filters_workspace_and_maps_waiting_without_payloads(self) -> None:
        output = (FIXTURES / "workbuddy-ps.json").read_text(encoding="utf-8")
        output = output.replace("__WORKSPACE__", str(WORKSPACE).replace("\\", "\\\\"))
        runner_calls = []

        def runner(command, timeout):
            runner_calls.append((tuple(command), timeout))
            return 0, output, ""

        result = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=runner,
                stale_after_seconds=1_000_000_000.0,
            )
        ).collect()

        self.assertEqual(1, len(result.agents))
        self.assertEqual("WorkBuddy", result.agents[0].name)
        self.assertEqual("waiting", result.agents[0].status)
        self.assertEqual("healthy", result.health.status)
        self.assertEqual([(("codebuddy", "ps", "--json"), 3.0)], runner_calls)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("initialPrompt", serialized)
        self.assertNotIn("Bearer private", serialized)
        self.assertNotIn("reasoning: private", serialized)

    def test_workbuddy_maps_busy_epoch_heartbeat_and_safe_job_options(self) -> None:
        jobs = self.root / "jobs"
        state = jobs / "01a090cb" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(
            json.dumps(
                {
                    "initialPrompt": "developer instructions: private",
                    "intent": "reasoning: private",
                    "respawnArgs": [
                        "--prompt",
                        "raw private prompt",
                        "--model",
                        "glm-5.3-flash",
                        "--effort",
                        "high",
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = json.dumps(
            [
                {
                    "sessionId": "wb-session",
                    "cwd": "\\\\?\\" + str(WORKSPACE),
                    "name": "prompt: private",
                    "status": "busy",
                    "lastHeartbeat": 1_000_000,
                    "meta": {"jobId": "01a090cb"},
                }
            ]
        )

        result = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                workbuddy_jobs_dir=jobs,
                clock=lambda: 1001.0,
                stale_after_seconds=5.0,
            )
        ).collect()

        agent = result.agents[0]
        self.assertEqual("WorkBuddy", agent.name)
        self.assertEqual("running", agent.status)
        self.assertEqual("1970-01-01T00:16:40+00:00", agent.updated_at)
        self.assertEqual("glm-5.3-flash", agent.model)
        self.assertEqual("high", agent.effort)
        self.assertNotIn("private", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_workbuddy_marks_stale_active_epoch_heartbeat_unknown(self) -> None:
        output = json.dumps(
            [
                {
                    "sessionId": "wb-stale",
                    "cwd": str(WORKSPACE),
                    "status": "busy",
                    "lastHeartbeat": 1_000_000,
                }
            ]
        )

        result = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                clock=lambda: 1010.0,
                stale_after_seconds=5.0,
            )
        ).collect()

        self.assertEqual("unknown", result.agents[0].status)
        self.assertEqual("证据已陈旧", result.agents[0].status_evidence)

    def test_workbuddy_default_window_keeps_ten_second_busy_heartbeat_running(self) -> None:
        output = json.dumps(
            [
                {
                    "sessionId": "wb-window",
                    "cwd": str(WORKSPACE),
                    "status": "busy",
                    "lastHeartbeat": 1_000_000,
                }
            ]
        )

        fresh = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                clock=lambda: 1010.0,
            )
        ).collect()
        stale = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                clock=lambda: 1031.0,
            )
        ).collect()

        self.assertEqual("running", fresh.agents[0].status)
        self.assertEqual("unknown", stale.agents[0].status)
        self.assertEqual("证据已陈旧", stale.agents[0].status_evidence)

    def test_workbuddy_default_runner_resolves_cmd_and_decodes_utf8(self) -> None:
        output = json.dumps(
            [{"sessionId": "wb-utf8", "cwd": str(WORKSPACE), "status": "idle"}],
            ensure_ascii=False,
        )
        observed: dict[str, object] = {}

        def run(command, **kwargs):
            observed["command"] = command
            observed["kwargs"] = kwargs
            return mock.Mock(returncode=0, stdout=output, stderr="")

        with (
            mock.patch("observer.adapters.workbuddy.shutil.which", return_value=r"C:\tools\codebuddy.CMD"),
            mock.patch("observer.adapters.workbuddy.subprocess.run", side_effect=run),
        ):
            result = WorkBuddySource(ObserverConfig(workspace=WORKSPACE)).collect()

        self.assertEqual("healthy", result.health.status)
        self.assertEqual(r"C:\tools\codebuddy.CMD", observed["command"][0])
        self.assertEqual("utf-8", observed["kwargs"]["encoding"])

    def test_workbuddy_rejects_invalid_ids_and_does_not_scan_unreferenced_jobs(self) -> None:
        jobs = self.root / "jobs"
        state = jobs / "safe-job" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(
            json.dumps({"respawnArgs": ["--model", "secret model", "--effort=high"]}),
            encoding="utf-8",
        )
        output = json.dumps(
            [
                {"sessionId": "prompt private", "cwd": str(WORKSPACE), "status": "busy"},
                {
                    "sessionId": "wb-safe",
                    "cwd": str(WORKSPACE),
                    "status": "busy",
                    "meta": {"jobId": "safe-job"},
                },
            ]
        )

        result = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                workbuddy_jobs_dir=jobs,
            )
        ).collect()

        self.assertEqual(["workbuddy:wb-safe"], [agent.id for agent in result.agents])
        self.assertEqual("[内容已隐藏]", result.agents[0].model)
        self.assertIsNone(result.agents[0].effort)

    def test_workbuddy_returns_only_latest_events_in_stable_order(self) -> None:
        output = json.dumps(
            [
                {
                    "sessionId": f"wb-{index}",
                    "cwd": str(WORKSPACE),
                    "status": "busy",
                    "lastHeartbeat": (1000 + index) * 1000,
                }
                for index in (2, 0, 1)
            ]
        )

        result = WorkBuddySource(
            ObserverConfig(
                workspace=WORKSPACE,
                runner=lambda command, timeout: (0, output, ""),
                clock=lambda: 1003.0,
                stale_after_seconds=10.0,
                max_events_per_source=2,
            )
        ).collect()

        self.assertEqual(
            ["workbuddy:wb-1:ps", "workbuddy:wb-2:ps"],
            [event.id for event in result.events],
        )

    def test_invalid_workbuddy_json_degrades_without_echoing_output(self) -> None:
        def runner(command, timeout):
            return 0, '{"Authorization":"Bearer private"', ""

        result = WorkBuddySource(
            ObserverConfig(workspace=WORKSPACE, runner=runner)
        ).collect()

        self.assertEqual("degraded", result.health.status)
        self.assertEqual((), result.agents)
        self.assertNotIn("Bearer", result.health.detail)

    def test_collect_snapshot_aggregates_all_source_health(self) -> None:
        sessions = self.root / "dsh"
        sessions.mkdir()
        codex = self.root / "codex"
        codex.mkdir()

        snapshot = collect_snapshot(
            ObserverConfig(
                workspace=WORKSPACE,
                coordination_status_path=FIXTURES / "coordination-status.md",
                handoffs_dir=self.root / "handoffs",
                codex_sessions_root=codex,
                dsh_sessions_dir=sessions,
                runner=lambda command, timeout: (0, "[]", ""),
                clock=lambda: 0.0,
            )
        )

        self.assertEqual("live", snapshot.connection)
        self.assertEqual(
            ["coordination", "codex", "dsh", "workbuddy"],
            [health.source for health in snapshot.source_health],
        )
        self.assertEqual("1970-01-01T00:00:00+00:00", snapshot.generated_at)


if __name__ == "__main__":
    unittest.main()
