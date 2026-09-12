"""Allowlisted reader for DSH per-session projection JSON."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..model import AdapterResult, AgentState, ObserverConfig, SourceHealth, TraceEvent
from ..redaction import (
    HIDDEN_TEXT,
    canonical_path_key,
    sanitize_identifier,
    sanitize_model_name,
)


def _same_workspace(left: object, right: Path) -> bool:
    return isinstance(left, str) and canonical_path_key(left) == canonical_path_key(right)


def _row_value(rows: dict[str, object], name: str) -> tuple[bool, object]:
    if name not in rows:
        return False, None
    row = rows.get(name)
    if not isinstance(row, dict) or "val" not in row:
        return False, None
    return True, row.get("val")


class DshSource:
    def __init__(self, config: ObserverConfig) -> None:
        self._config = config

    def collect(self) -> AdapterResult:
        root = self._config.dsh_sessions_dir or (
            Path.home() / ".dsh" / "storages" / "session_projcache" / "sessions"
        )
        if not root.exists():
            return AdapterResult(
                health=SourceHealth(source="dsh", status="unknown", detail="DSH 会话投影目录不存在")
            )
        agents: list[AgentState] = []
        event_pairs: list[tuple[float, TraceEvent]] = []
        degraded = False
        try:
            candidates: list[tuple[float, Path]] = []
            for path in root.glob("*.json"):
                try:
                    candidates.append((path.stat().st_mtime, path))
                except OSError:
                    degraded = True
        except OSError:
            return self._degraded("DSH 会话投影目录不可读")
        candidates.sort(key=lambda item: (item[0], canonical_path_key(item[1])), reverse=True)
        for _mtime, path in candidates[: max(0, self._config.max_dsh_sessions)]:
            parsed, file_degraded = self._read_projection(path)
            degraded = degraded or file_degraded
            if parsed is None:
                continue
            agent, event = parsed
            agents.append(agent)
            event_pairs.append((_mtime, event))
        event_limit = max(0, self._config.max_events_per_source)
        ordered_events = [event for _mtime, event in sorted(event_pairs, key=lambda item: item[0])]
        events = ordered_events[-event_limit:] if event_limit else []
        return AdapterResult(
            health=SourceHealth(
                source="dsh",
                status="degraded" if degraded else "healthy",
                detail="DSH 部分会话投影无效" if degraded else "已读取 DSH 会话投影",
            ),
            agents=tuple(agents),
            events=tuple(events),
        )

    def _read_projection(
        self, path: Path
    ) -> tuple[tuple[AgentState, TraceEvent] | None, bool]:
        try:
            stat = path.stat()
            if stat.st_size > self._config.max_file_bytes:
                return None, True
            raw = path.read_bytes()
            payload = json.loads(raw)
            modified_at = stat.st_mtime
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, True
        if not isinstance(payload, dict):
            return None, True
        record = payload.get("record")
        if not isinstance(record, dict):
            return None, True
        identity = record.get("identity")
        rows = record.get("rows")
        if not isinstance(identity, dict) or not isinstance(rows, dict):
            return None, True
        if not _same_workspace(identity.get("cwd"), self._config.workspace):
            return None, False
        identity_id = identity.get("id") or identity.get("sessionId") or identity.get("session_id")
        raw_id = identity_id if isinstance(identity_id, str) and identity_id else path.stem
        safe_id = sanitize_identifier(raw_id)
        if safe_id == HIDDEN_TEXT:
            return None, True

        model = None
        effort = None
        _, selection = _row_value(rows, "modelSelection")
        if isinstance(selection, dict):
            last_used = selection.get("lastUsed")
            if isinstance(last_used, dict):
                provider = last_used.get("provider")
                raw_model = last_used.get("model")
                if isinstance(raw_model, str):
                    combined_model = (
                        f"{provider}/{raw_model}"
                        if isinstance(provider, str) and provider and "/" not in raw_model
                        else raw_model
                    )
                    model = sanitize_model_name(combined_model)
                raw_effort = last_used.get("reasoningEffort")
                if isinstance(raw_effort, str):
                    safe_effort = sanitize_identifier(raw_effort)
                    effort = safe_effort

        boundary_present, boundary = _row_value(rows, "turnBoundary")
        _, stats = _row_value(rows, "sessionStats")
        open_turn_present = isinstance(boundary, dict) and "openTurnStartSeq" in boundary
        open_turn = boundary.get("openTurnStartSeq") if isinstance(boundary, dict) else None
        open_step = stats.get("openStep") if isinstance(stats, dict) else None
        pending = stats.get("pendingCalls") if isinstance(stats, dict) else None
        has_pending = isinstance(pending, (dict, list)) and bool(pending)
        active = open_turn is not None or open_step is not None or has_pending
        terminal = (
            isinstance(boundary, dict)
            and (
                boundary.get("lastTurn") is not None
                or boundary.get("lastStepBoundary") is not None
            )
        ) or (isinstance(stats, dict) and stats.get("lastTurn") is not None)
        now = self._config.clock() if self._config.clock else time.time()
        stale = max(0.0, now - modified_at) > self._config.stale_after_seconds
        if active and stale:
            status, evidence = "unknown", "证据已陈旧"
        elif active:
            status, evidence = "running", "DSH 开放 turn/step"
        elif terminal and boundary_present and open_turn_present and open_turn is None:
            status, evidence = "completed", "DSH turn/step 已结束"
        else:
            status, evidence = "unknown", "暂无证据"
        updated_at = datetime.fromtimestamp(modified_at, timezone.utc).isoformat()
        agent_id = f"dsh:{safe_id}"
        agent = AgentState(
            id=agent_id,
            name="DSH",
            kind="dsh",
            status=status,
            status_evidence=evidence,
            model=model,
            effort=effort,
            cwd=str(self._config.workspace),
            updated_at=updated_at,
        )
        event = TraceEvent(
            id=f"{agent_id}:projection",
            source="dsh",
            agent_id=agent_id,
            kind="lifecycle",
            status=status,
            title="DSH 会话投影",
            detail="仅展示 DSH 生命周期元数据",
            evidence=evidence,
        )
        return (agent, event), False

    @staticmethod
    def _degraded(detail: str) -> AdapterResult:
        return AdapterResult(health=SourceHealth(source="dsh", status="degraded", detail=detail))
