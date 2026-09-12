"""Read declared coordination state from allowlisted Markdown table cells."""

from __future__ import annotations

import re

from ..model import AdapterResult, AgentState, ObserverConfig, SourceHealth, TaskState
from ..redaction import HIDDEN_TEXT, sanitize_identifier

_KNOWN_AGENTS = {
    "codex": ("Codex", "codex"),
    "dsh": ("DSH", "dsh"),
    "workbuddy": ("WorkBuddy", "workbuddy"),
}
_HANDOFF_OWNER_SUFFIXES = {f"-{kind}": kind for kind in _KNOWN_AGENTS}
_STATUS_TOKEN = re.compile(
    r"^\s*(IN_PROGRESS|RUNNING|DONE|COMPLETED|ABORTED|CANCELLED|CANCELED|PLANNED|WAITING|BLOCKED|FAILED)\b",
    flags=re.IGNORECASE,
)


def _normalized_status(value: str) -> str:
    match = _STATUS_TOKEN.match(value)
    if match:
        token = match.group(1).casefold()
        if token in {"in_progress", "running"}:
            return "running"
        if token in {"done", "completed"}:
            return "completed"
        if token in {"aborted", "cancelled", "canceled"}:
            return "aborted"
        if token in {"planned", "waiting", "blocked", "failed"}:
            return token
        return "unknown"
    lowered = value.strip().casefold()
    for prefix, status in (
        ("在线", "running"),
        ("进行中", "running"),
        ("协调中", "running"),
        ("已完成", "completed"),
        ("已中止", "aborted"),
        ("等待", "waiting"),
        ("待授权", "waiting"),
    ):
        if lowered.startswith(prefix):
            return status
    return "unknown"


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _handoff_identity(stem: str) -> tuple[str, str | None]:
    lowered = stem.casefold()
    for suffix, owner_kind in _HANDOFF_OWNER_SUFFIXES.items():
        if lowered.endswith(suffix):
            task_id = sanitize_identifier(stem[: -len(suffix)])
            return task_id, owner_kind
    return sanitize_identifier(stem), None


class CoordinationSource:
    def __init__(self, config: ObserverConfig) -> None:
        self._config = config

    def collect(self) -> AdapterResult:
        path = self._config.coordination_status_path or (
            self._config.workspace / ".agent-coordination" / "status.md"
        )
        try:
            workspace = self._config.workspace.resolve()
            resolved = path.resolve()
            if not resolved.is_relative_to(workspace):
                return self._degraded("协调状态路径超出工作区")
            raw = resolved.read_bytes()
            if len(raw) > self._config.max_file_bytes:
                return self._degraded("协调状态文件超过读取上限")
            text = raw.decode("utf-8")
        except FileNotFoundError:
            return AdapterResult(
                health=SourceHealth(
                    source="coordination", status="unknown", detail="协调状态文件不存在"
                )
            )
        except (OSError, UnicodeError):
            return self._degraded("无法读取协调状态")

        agents: list[AgentState] = []
        tasks: list[TaskState] = []
        section = "agents"
        for line in text.splitlines():
            if line.startswith("## ") and "任务" in line:
                section = "tasks"
                continue
            if not line.lstrip().startswith("|"):
                continue
            cells = _table_cells(line)
            if _is_separator(cells) or not cells:
                continue
            if section == "agents":
                if cells[0].casefold() == "agent" or len(cells) < 2:
                    continue
                known = _KNOWN_AGENTS.get(cells[0].strip().casefold())
                if known is None:
                    continue
                name, kind = known
                status = _normalized_status(cells[1])
                agents.append(
                    AgentState(
                        id=f"coordination:{kind}",
                        name=name,
                        kind=kind,
                        status=status,
                        status_evidence="协调状态声明" if status != "unknown" else "暂无证据",
                    )
                )
            else:
                if cells[0] in {"任务 ID", "Task ID"} or len(cells) < 5:
                    continue
                task_id = sanitize_identifier(cells[0])
                if task_id == HIDDEN_TEXT:
                    continue
                owner = _KNOWN_AGENTS.get(cells[1].strip().casefold())
                status = _normalized_status(cells[2])
                tasks.append(
                    TaskState(
                        id=task_id,
                        title=task_id,
                        owner_id=f"coordination:{owner[1]}" if owner else None,
                        status=status,
                        status_evidence="协调状态声明" if status != "unknown" else "暂无证据",
                    )
                )

        handoffs = self._config.handoffs_dir or (
            self._config.workspace / ".agent-coordination" / "handoffs"
        )
        health_status = "healthy"
        health_detail = "已读取协调状态"
        try:
            handoff_root = handoffs.resolve()
            if handoff_root.is_relative_to(workspace) and handoff_root.is_dir():
                known_ids = {task.id for task in tasks}
                for handoff in sorted(handoff_root.glob("*.md")):
                    task_id, owner_kind = _handoff_identity(handoff.stem)
                    if task_id == HIDDEN_TEXT:
                        continue
                    if task_id not in known_ids:
                        tasks.append(
                            TaskState(
                                id=task_id,
                                title=task_id,
                                owner_id=(
                                    f"coordination:{owner_kind}" if owner_kind else None
                                ),
                            )
                        )
                        known_ids.add(task_id)
        except OSError:
            health_status = "degraded"
            health_detail = "已读取协调状态，但 handoff 目录不可读"

        return AdapterResult(
            health=SourceHealth(
                source="coordination", status=health_status, detail=health_detail
            ),
            agents=tuple(agents),
            tasks=tuple(tasks),
        )

    @staticmethod
    def _degraded(detail: str) -> AdapterResult:
        return AdapterResult(
            health=SourceHealth(source="coordination", status="degraded", detail=detail)
        )
