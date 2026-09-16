"""Read sanitized workflow events written by the deterministic workflow CLI."""

from __future__ import annotations

import json

from ..model import AdapterResult, ObserverConfig, SourceHealth, TaskState, TraceEvent
from ..redaction import HIDDEN_TEXT, sanitize_identifier, sanitize_text

_AGENTS = {"codex", "dsh", "workbuddy"}
_STATUSES = {"planned", "running", "waiting", "blocked", "failed", "completed", "aborted"}
_KINDS = {"task", "progress", "delivery"}


def _strings(value: object, *, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result = []
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        safe = sanitize_text(item, 500)
        if safe != HIDDEN_TEXT:
            result.append(safe)
    return tuple(result)


def _text(value: object, limit: int) -> str:
    return sanitize_text(value if isinstance(value, str) else "", limit)


class RuntimeSource:
    def __init__(self, config: ObserverConfig) -> None:
        self._config = config

    def collect(self) -> AdapterResult:
        path = self._config.runtime_events_path or (
            self._config.workspace / ".agent-coordination" / "runtime" / "events.jsonl"
        )
        try:
            workspace = self._config.workspace.resolve()
            resolved = path.resolve()
            if not resolved.is_relative_to(workspace):
                return self._degraded("运行时事件路径超出工作区")
            raw = resolved.read_bytes()
            if len(raw) > self._config.max_runtime_bytes:
                return self._degraded("运行时事件文件超过读取上限")
        except FileNotFoundError:
            return AdapterResult(
                health=SourceHealth(source="runtime", status="unknown", detail="运行时事件尚未出现")
            )
        except OSError:
            return self._degraded("无法读取运行时事件")

        events: list[TraceEvent] = []
        tasks: dict[str, TaskState] = {}
        last_event: dict[tuple[str, str], str] = {}
        degraded = False
        for raw_line in raw.splitlines():
            try:
                item = json.loads(raw_line.decode("utf-8"))
                event = self._parse_event(item, last_event)
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                degraded = True
                continue
            if event is None:
                degraded = True
                continue
            events.append(event)
            key = (event.task_id or "", event.source)
            last_event[key] = event.id
            existing = tasks.get(event.task_id or "")
            title = event.title if event.kind == "task" else (existing.title if existing else event.task_id or "unknown")
            tasks[event.task_id or ""] = TaskState(
                id=event.task_id or "unknown",
                title=title,
                owner_id=event.agent_id,
                status=event.status,
                status_evidence=event.evidence,
                source="runtime",
                updated_at=event.ended_at or event.started_at,
                phase=event.phase,
                last_progress=event.detail,
            )
        event_limit = max(0, self._config.max_events_per_source)
        return AdapterResult(
            health=SourceHealth(
                source="runtime",
                status="degraded" if degraded else "healthy",
                detail="部分运行时事件无效" if degraded else "已读取结构化进度与交付",
            ),
            tasks=tuple(tasks.values()),
            events=tuple(events[-event_limit:] if event_limit else ()),
        )

    @staticmethod
    def _parse_event(
        item: object, last_event: dict[tuple[str, str], str]
    ) -> TraceEvent | None:
        if not isinstance(item, dict) or item.get("schema_version") != "1":
            return None
        event_id = sanitize_identifier(item.get("id"))
        task_id = sanitize_identifier(item.get("task_id"))
        agent = sanitize_identifier(item.get("agent"))
        kind = item.get("kind")
        status = item.get("status")
        if (
            event_id == HIDDEN_TEXT
            or task_id == HIDDEN_TEXT
            or agent not in _AGENTS
            or kind not in _KINDS
            or status not in _STATUSES
        ):
            return None
        phase = _text(item.get("phase"), 80)
        title = _text(item.get("title"), 160)
        detail = _text(item.get("detail"), 1200)
        timestamp = item.get("timestamp") if isinstance(item.get("timestamp"), str) else None
        evidence_refs = _strings(item.get("evidence"))
        delivery_value = item.get("deliverables")
        delivery: dict[object, object] = delivery_value if isinstance(delivery_value, dict) else {}
        handoff_value = delivery.get("handoff")
        handoff = sanitize_text(handoff_value, 500) if isinstance(handoff_value, str) else None
        parent_id = last_event.get((task_id, agent))
        return TraceEvent(
            id=event_id,
            source=agent,
            agent_id=f"coordination:{agent}",
            parent_id=parent_id,
            task_id=task_id,
            kind=kind,
            status=status,
            title=title,
            detail=detail,
            phase=phase,
            started_at=timestamp if status not in {"completed", "failed", "aborted"} else None,
            ended_at=timestamp if status in {"completed", "failed", "aborted"} else None,
            evidence="结构化工作流事件",
            evidence_refs=evidence_refs,
            deliverables=_strings(delivery.get("files")),
            tests=_strings(delivery.get("tests")),
            artifacts=_strings(delivery.get("artifacts")),
            handoff=handoff,
            concerns=_strings(delivery.get("risks")) + _strings(delivery.get("unresolved")),
        )

    @staticmethod
    def _degraded(detail: str) -> AdapterResult:
        return AdapterResult(
            health=SourceHealth(source="runtime", status="degraded", detail=detail)
        )
