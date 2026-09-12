"""Read WorkBuddy process liveness without prompts, logs, or payloads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
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


def _default_runner(command: Sequence[str], timeout: float) -> tuple[int, str, str]:
    resolved = list(command)
    if os.name == "nt" and resolved and Path(resolved[0]).name.casefold() == "codebuddy":
        executable = shutil.which("codebuddy.CMD") or shutil.which("codebuddy")
        if executable is None:
            raise FileNotFoundError("codebuddy.CMD was not found")
        resolved[0] = executable
    completed = subprocess.run(
        resolved,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _status(value: object, waiting: bool) -> str:
    if waiting:
        return "waiting"
    if not isinstance(value, str):
        return "unknown"
    lowered = value.casefold()
    if lowered in {"waiting", "blocked", "awaiting", "needs_attention"}:
        return "waiting"
    if lowered in {"running", "active", "online", "busy"}:
        return "running"
    if lowered in {"completed", "complete", "done", "exited"}:
        return "completed"
    if lowered in {"aborted", "cancelled", "canceled", "stopped"}:
        return "aborted"
    return "unknown"


def _heartbeat(value: object) -> tuple[str | None, float | None]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        epoch_seconds = float(value) / 1000.0
        try:
            return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat(), epoch_seconds
        except (OverflowError, OSError, ValueError):
            return None, None
    if isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None, None
        if parsed.tzinfo is None:
            return None, None
        return parsed.astimezone(timezone.utc).isoformat(), parsed.timestamp()
    return None, None


class WorkBuddySource:
    def __init__(self, config: ObserverConfig) -> None:
        self._config = config

    def collect(self) -> AdapterResult:
        runner = self._config.runner or _default_runner
        try:
            return_code, stdout, _stderr = runner(
                self._config.workbuddy_command, self._config.command_timeout_seconds
            )
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return self._degraded("WorkBuddy 状态命令失败")
        if return_code != 0:
            return self._degraded("WorkBuddy 状态命令返回非零状态")
        try:
            payload = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            return self._degraded("WorkBuddy 状态 JSON 无效")
        if isinstance(payload, dict):
            candidates = payload.get("processes", payload.get("sessions"))
        else:
            candidates = payload
        if not isinstance(candidates, list):
            return self._degraded("WorkBuddy 状态 JSON 结构无效")
        agents: list[AgentState] = []
        events: list[TraceEvent] = []
        now = self._config.clock() if self._config.clock else time.time()
        for item in candidates:
            if not isinstance(item, dict) or not _same_workspace(
                item.get("cwd"), self._config.workspace
            ):
                continue
            raw_id = item.get("sessionId")
            if not isinstance(raw_id, str) or not raw_id:
                pid = item.get("pid")
                raw_id = str(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
            safe_id = sanitize_identifier(raw_id)
            if safe_id == HIDDEN_TEXT:
                continue
            waiting = bool(item.get("waitingFor"))
            status = _status(item.get("status"), waiting)
            updated_at, heartbeat_epoch = _heartbeat(item.get("lastHeartbeat"))
            if status in {"running", "waiting"} and heartbeat_epoch is None:
                status, evidence = "unknown", "暂无心跳证据"
            elif (
                status in {"running", "waiting"}
                and heartbeat_epoch is not None
                and max(0.0, now - heartbeat_epoch) > self._config.stale_after_seconds
            ):
                status, evidence = "unknown", "证据已陈旧"
            else:
                evidence = (
                    "WorkBuddy ps 等待状态"
                    if status == "waiting"
                    else "WorkBuddy ps 进程状态"
                )
            model, effort = self._job_options(item.get("meta"))
            agent_id = f"workbuddy:{safe_id}"
            agents.append(
                AgentState(
                    id=agent_id,
                    name="WorkBuddy",
                    kind="workbuddy",
                    status=status,
                    status_evidence=evidence,
                    model=model,
                    effort=effort,
                    cwd=str(self._config.workspace),
                    updated_at=updated_at,
                )
            )
            events.append(
                TraceEvent(
                    id=f"{agent_id}:ps",
                    source="workbuddy",
                    agent_id=agent_id,
                    kind="lifecycle",
                    status=status,
                    title="WorkBuddy 进程状态",
                    detail="仅展示 WorkBuddy 生命周期元数据",
                    ended_at=updated_at,
                    evidence=evidence,
                )
            )
        events.sort(key=lambda event: (event.ended_at or "", event.id))
        event_limit = max(0, self._config.max_events_per_source)
        events = events[-event_limit:] if event_limit else []
        return AdapterResult(
            health=SourceHealth(source="workbuddy", status="healthy", detail="已读取 WorkBuddy 进程状态"),
            agents=tuple(agents),
            events=tuple(events),
        )

    def _job_options(self, meta: object) -> tuple[str | None, str | None]:
        if not isinstance(meta, dict):
            return None, None
        job_id = sanitize_identifier(meta.get("jobId"))
        if job_id == HIDDEN_TEXT:
            return None, None
        root = self._config.workbuddy_jobs_dir or (Path.home() / ".codebuddy" / "jobs")
        try:
            root = root.resolve()
            state_path = (root / job_id / "state.json").resolve()
            if not state_path.is_relative_to(root):
                return None, None
            stat = state_path.stat()
            if stat.st_size > self._config.max_file_bytes:
                return None, None
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, None
        if not isinstance(state, dict):
            return None, None
        args = state.get("respawnArgs")
        if not isinstance(args, list):
            return None, None
        model = None
        effort = None
        for index, token in enumerate(args[:-1]):
            value = args[index + 1]
            if not isinstance(token, str) or not isinstance(value, str):
                continue
            if token == "--model" and model is None:
                model = sanitize_model_name(value)
            elif token == "--effort" and effort is None:
                effort = sanitize_identifier(value)
        return model, effort

    @staticmethod
    def _degraded(detail: str) -> AdapterResult:
        return AdapterResult(
            health=SourceHealth(source="workbuddy", status="degraded", detail=detail)
        )
