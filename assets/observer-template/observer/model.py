"""Immutable normalized records shared by all observer sources."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

UNKNOWN = "unknown"
NO_EVIDENCE = "暂无证据"


class Runner(Protocol):
    def __call__(
        self, command: Sequence[str], timeout: float
    ) -> tuple[int, str, str]: ...


@dataclass(frozen=True, slots=True)
class AgentState:
    id: str
    name: str
    kind: str
    parent_id: str | None = None
    status: str = UNKNOWN
    status_evidence: str = NO_EVIDENCE
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class TaskState:
    id: str
    title: str
    owner_id: str | None = None
    status: str = UNKNOWN
    status_evidence: str = NO_EVIDENCE
    source: str = "coordination"
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class TraceEvent:
    id: str
    source: str
    agent_id: str
    kind: str
    title: str
    parent_id: str | None = None
    task_id: str | None = None
    status: str = UNKNOWN
    detail: str = NO_EVIDENCE
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    evidence: str = NO_EVIDENCE
    exit_code: int | None = None
    tool_name: str | None = None


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    status: str = UNKNOWN
    detail: str = NO_EVIDENCE
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterResult:
    health: SourceHealth
    agents: tuple[AgentState, ...] = ()
    tasks: tuple[TaskState, ...] = ()
    events: tuple[TraceEvent, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in ("agents", "tasks", "events"):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class Snapshot:
    generated_at: str
    workspace: str
    connection: str = "live"
    agents: tuple[AgentState, ...] = ()
    tasks: tuple[TaskState, ...] = ()
    events: tuple[TraceEvent, ...] = ()
    source_health: tuple[SourceHealth, ...] = ()
    schema_version: str = "1"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in ("agents", "tasks", "events", "source_health"):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class ObserverConfig:
    workspace: Path
    coordination_status_path: Path | None = None
    handoffs_dir: Path | None = None
    codex_sessions_root: Path | None = None
    dsh_workspace_index: Path | None = None
    dsh_sessions_dir: Path | None = None
    workbuddy_command: tuple[str, ...] = ("codebuddy", "ps", "--json")
    runner: Runner | None = None
    clock: Callable[[], float] | None = None
    stale_after_seconds: float = 30.0
    max_file_bytes: int = 1_048_576
    max_codex_sessions: int = 12
    max_dsh_sessions: int = 12
    max_events_per_source: int = 80
    codex_head_bytes: int = 65_536
    codex_tail_bytes: int = 1_048_576
    workbuddy_jobs_dir: Path | None = None
    command_timeout_seconds: float = 3.0
