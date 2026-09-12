"""Metadata-only adapter for recent Codex rollout lifecycle records."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..model import AdapterResult, AgentState, ObserverConfig, SourceHealth, TraceEvent
from ..redaction import (
    HIDDEN_TEXT,
    canonical_path_key,
    sanitize_identifier,
    sanitize_model_name,
    sanitize_tool_name,
)

_TOOL_ITEM_TYPES = {
    "CommandExecution",
    "McpToolCall",
    "CollabAgentToolCall",
    "FileChange",
}
_ITEM_STATUSES = {"completed", "failed", "aborted", "running", "waiting"}


def _same_workspace(left: object, right: Path) -> bool:
    return isinstance(left, str) and canonical_path_key(left) == canonical_path_key(right)


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _complete_json_lines(path: Path, size: int, head_limit: int, tail_limit: int) -> list[bytes]:
    """Read at most head_limit + tail_limit bytes without decoding partial UTF-8."""

    head_limit = max(1, head_limit)
    tail_limit = max(1, tail_limit)
    with path.open("rb") as stream:
        if size <= head_limit + tail_limit:
            chunks = [stream.read(size)]
        else:
            head = stream.read(head_limit)
            head_end = head.rfind(b"\n")
            head = head[: head_end + 1] if head_end >= 0 else b""

            tail_offset = max(0, size - tail_limit)
            stream.seek(max(0, tail_offset - 1))
            preceding = stream.read(1) if tail_offset else b"\n"
            tail = stream.read(tail_limit)
            if preceding != b"\n":
                tail_start = tail.find(b"\n")
                tail = tail[tail_start + 1 :] if tail_start >= 0 else b""
            chunks = [head, tail]

    return [line for chunk in chunks for line in chunk.splitlines(keepends=True) if line.strip()]


def _limit_events_with_ancestors(
    events: list[TraceEvent], limit: int
) -> list[TraceEvent]:
    """Keep newest events without separating them from available ancestors."""

    if limit <= 0:
        return []
    by_id = {event.id: event for event in events}
    selected: set[str] = set()
    for event in reversed(events):
        chain: list[TraceEvent] = []
        cursor = event
        visited: set[str] = set()
        while cursor.id not in selected and cursor.id not in visited:
            visited.add(cursor.id)
            chain.append(cursor)
            if cursor.parent_id is None or cursor.parent_id not in by_id:
                break
            cursor = by_id[cursor.parent_id]
        missing = [item for item in chain if item.id not in selected]
        if len(selected) + len(missing) > limit:
            continue
        selected.update(item.id for item in missing)
        if len(selected) == limit:
            break
    return [event for event in events if event.id in selected]


class CodexSource:
    def __init__(self, config: ObserverConfig) -> None:
        self._config = config

    def collect(self) -> AdapterResult:
        root = self._config.codex_sessions_root or (Path.home() / ".codex" / "sessions")
        if not root.exists():
            return AdapterResult(
                health=SourceHealth(source="codex", status="unknown", detail="Codex 会话目录不存在")
            )
        try:
            candidates: list[tuple[float, Path, int]] = []
            degraded = False
            for path in root.rglob("rollout-*.jsonl"):
                try:
                    stat = path.stat()
                except OSError:
                    degraded = True
                    continue
                candidates.append((stat.st_mtime, path, stat.st_size))
        except OSError:
            return self._degraded("Codex 会话目录不可读")

        candidates.sort(key=lambda item: (item[0], canonical_path_key(item[1])), reverse=True)
        session_limit = max(0, self._config.max_codex_sessions)
        parsed_sessions: dict[
            str, tuple[float, AgentState, tuple[TraceEvent, ...]]
        ] = {}
        recent_ids: list[str] = []

        def add_candidate(candidate: tuple[float, Path, int], *, recent: bool) -> None:
            nonlocal degraded
            mtime, path, size = candidate
            rollout, file_degraded = self._read_rollout(path, size)
            degraded = degraded or file_degraded
            if rollout is None:
                return
            agent, file_events = rollout
            session_id = agent.id.removeprefix("codex:")
            if session_id not in parsed_sessions:
                parsed_sessions[session_id] = (mtime, agent, file_events)
                if recent:
                    recent_ids.append(session_id)

        for candidate in candidates[:session_limit]:
            add_candidate(candidate, recent=True)

        # A busy root task can have an old file mtime while newer child sessions
        # keep updating. Read only the referenced ancestor files and reserve
        # slots for them so the displayed agent/call hierarchy stays connected.
        max_parsed = session_limit * 2
        for _depth in range(4):
            if len(parsed_sessions) >= max_parsed:
                break
            missing_parents = {
                agent.parent_id
                for _mtime, agent, _events in parsed_sessions.values()
                if agent.parent_id and agent.parent_id not in parsed_sessions
            }
            matches = [
                candidate
                for candidate in candidates
                if any(candidate[1].stem.endswith(parent) for parent in missing_parents)
            ]
            if not matches:
                break
            before = len(parsed_sessions)
            for candidate in matches:
                if len(parsed_sessions) >= max_parsed:
                    break
                add_candidate(candidate, recent=False)
            if len(parsed_sessions) == before:
                break

        required_ancestors: set[str] = set()
        frontier = [
            parsed_sessions[session_id][1].parent_id
            for session_id in recent_ids
            if session_id in parsed_sessions
        ]
        while frontier:
            parent = frontier.pop()
            if parent is None or parent in required_ancestors or parent not in parsed_sessions:
                continue
            required_ancestors.add(parent)
            frontier.append(parsed_sessions[parent][1].parent_id)

        selected_ids = sorted(
            required_ancestors,
            key=lambda session_id: parsed_sessions[session_id][0],
            reverse=True,
        )[:session_limit]
        for session_id in recent_ids:
            if len(selected_ids) >= session_limit:
                break
            if session_id not in selected_ids:
                selected_ids.append(session_id)

        selected_groups = sorted(
            (parsed_sessions[session_id] for session_id in selected_ids),
            key=lambda item: item[0],
            reverse=True,
        )
        agents = [agent for _mtime, agent, _events in selected_groups]
        selected_session_events = {f"{agent.id}:session" for agent in agents}
        event_groups: list[tuple[float, tuple[TraceEvent, ...]]] = []
        for mtime, _agent, file_events in selected_groups:
            connected = tuple(
                replace(event, parent_id=None)
                if event.kind == "session"
                and event.parent_id is not None
                and event.parent_id not in selected_session_events
                else event
                for event in file_events
            )
            event_groups.append((mtime, connected))
        ordered_events = [
            event
            for _mtime, file_events in sorted(event_groups, key=lambda item: item[0])
            for event in file_events
        ]
        event_limit = max(0, self._config.max_events_per_source)
        events = _limit_events_with_ancestors(ordered_events, event_limit)
        return AdapterResult(
            health=SourceHealth(
                source="codex",
                status="degraded" if degraded else "healthy",
                detail="Codex 部分会话记录无效" if degraded else "已读取 Codex 生命周期元数据",
            ),
            agents=tuple(agents),
            events=tuple(events),
        )

    def _read_rollout(
        self, path: Path, size: int
    ) -> tuple[tuple[AgentState, tuple[TraceEvent, ...]] | None, bool]:
        try:
            lines = _complete_json_lines(
                path,
                size,
                self._config.codex_head_bytes,
                self._config.codex_tail_bytes,
            )
        except OSError:
            return None, True

        records: list[dict[str, object]] = []
        degraded = False
        for index, raw_line in enumerate(lines):
            try:
                value = json.loads(raw_line.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                is_unterminated_tail = index == len(lines) - 1 and not raw_line.endswith(
                    (b"\n", b"\r")
                )
                degraded = degraded or not is_unterminated_tail
                continue
            if isinstance(value, dict):
                records.append(value)

        meta = next((record for record in records if record.get("type") == "session_meta"), None)
        payload = meta.get("payload") if isinstance(meta, dict) else None
        if not isinstance(payload, dict) or not _same_workspace(
            payload.get("cwd"), self._config.workspace
        ):
            return None, degraded
        session_id = sanitize_identifier(payload.get("id"))
        if session_id == HIDDEN_TEXT:
            return None, True
        parent_value = sanitize_identifier(payload.get("parent_thread_id"))
        parent_id = parent_value if parent_value != HIDDEN_TEXT else None
        raw_model = payload.get("model")
        model = sanitize_model_name(raw_model) if raw_model is not None else None
        agent_id = f"codex:{session_id}"
        status = "unknown"
        evidence = "暂无证据"
        updated_at = None
        active_turns: set[str] = set()
        events: list[TraceEvent] = []
        session_event_id = f"{agent_id}:session"
        turn_event_ids: dict[str, str] = {}
        for position, record in enumerate(records):
            if record.get("type") != "event_msg":
                continue
            event_payload = record.get("payload")
            if not isinstance(event_payload, dict):
                continue
            event_type = event_payload.get("type")
            safe_time = _safe_timestamp(record.get("timestamp"))
            if safe_time is not None:
                updated_at = safe_time
            raw_turn_id = sanitize_identifier(event_payload.get("turn_id"))
            task_id = raw_turn_id if raw_turn_id != HIDDEN_TEXT else None
            if event_type in {"task_started", "task_complete", "turn_aborted"}:
                mapped = {
                    "task_started": ("running", "started", "任务已开始"),
                    "task_complete": ("completed", "completed", "任务已完成"),
                    "turn_aborted": ("aborted", "aborted", "任务已中止"),
                }[event_type]
                status, event_status, title = mapped
                evidence = f"Codex {event_type}"
                if task_id is not None:
                    if event_type == "task_started":
                        active_turns.add(task_id)
                    else:
                        active_turns.discard(task_id)
                event_id = f"{agent_id}:{task_id or position}:{event_type}"
                if event_type == "task_started" and task_id is not None:
                    event_parent_id = session_event_id
                    turn_event_ids[task_id] = event_id
                else:
                    event_parent_id = turn_event_ids.get(task_id or "", session_event_id)
                events.append(
                    TraceEvent(
                        id=event_id,
                        source="codex",
                        agent_id=agent_id,
                        parent_id=event_parent_id,
                        task_id=task_id,
                        kind="lifecycle",
                        status=event_status,
                        title=title,
                        detail=title,
                        started_at=safe_time if event_type == "task_started" else None,
                        ended_at=safe_time if event_type != "task_started" else None,
                        evidence=evidence,
                    )
                )
            elif event_type == "item_completed":
                event = self._tool_event(
                    event_payload.get("item"),
                    agent_id=agent_id,
                    parent_id=turn_event_ids.get(task_id or "", session_event_id),
                    task_id=task_id,
                    ended_at=safe_time,
                    position=position,
                )
                if event is not None:
                    events.append(event)
        if active_turns:
            status = "running"
            evidence = "Codex task_started"
        session_parent_id = f"codex:{parent_id}:session" if parent_id else None
        events.insert(
            0,
            TraceEvent(
                id=session_event_id,
                source="codex",
                agent_id=agent_id,
                parent_id=session_parent_id,
                kind="session",
                status=status,
                title="Codex Agent 会话",
                detail="仅展示会话层级元数据",
                evidence="Codex session_meta",
            ),
        )
        return (
            AgentState(
                id=agent_id,
                name="Codex",
                kind="codex",
                parent_id=parent_id,
                status=status,
                status_evidence=evidence,
                model=model,
                cwd=str(self._config.workspace),
                updated_at=updated_at,
            ),
            tuple(events),
        ), degraded

    @staticmethod
    def _tool_event(
        item: object,
        *,
        agent_id: str,
        parent_id: str | None,
        task_id: str | None,
        ended_at: str | None,
        position: int,
    ) -> TraceEvent | None:
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if item_type == "function_call":
            raw_tool_name = item.get("name")
            raw_call_id = item.get("call_id")
        elif item_type in _TOOL_ITEM_TYPES:
            raw_call_id = item.get("id")
            raw_tool_name = (
                item.get("tool")
                if item_type in {"McpToolCall", "CollabAgentToolCall"}
                else item_type
            )
        else:
            return None
        tool_name = sanitize_tool_name(raw_tool_name)
        call_id = sanitize_identifier(raw_call_id)
        if call_id == HIDDEN_TEXT:
            call_id = f"item-{position}"
        item_status = item.get("status")
        safe_status = item_status if item_status in _ITEM_STATUSES else "unknown"
        exit_code = item.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
        duration = item.get("duration")
        duration_ms = int(duration) if isinstance(duration, (int, float)) else None
        return TraceEvent(
            id=f"{agent_id}:{call_id}",
            source="codex",
            agent_id=agent_id,
            parent_id=parent_id,
            task_id=task_id,
            kind="tool",
            status=safe_status,
            title=f"工具调用：{tool_name}",
            detail="仅展示工具元数据",
            ended_at=ended_at,
            duration_ms=duration_ms,
            evidence="Codex item_completed",
            exit_code=exit_code,
            tool_name=tool_name,
        )

    @staticmethod
    def _degraded(detail: str) -> AdapterResult:
        return AdapterResult(health=SourceHealth(source="codex", status="degraded", detail=detail))
