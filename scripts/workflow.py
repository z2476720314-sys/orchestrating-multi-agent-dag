"""Write sanitized multi-agent task, progress, and delivery events."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "assets" / "observer-template"))

from observer.redaction import HIDDEN_TEXT, sanitize_text

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_AGENTS = ("codex", "dsh", "workbuddy")
_STATUSES = ("planned", "running", "waiting", "blocked", "failed", "completed", "aborted")
_MAX_LEDGER_BYTES = 8 * 1024 * 1024


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError("identifier must use letters, numbers, dot, colon, underscore, or hyphen")
    return value


def _workspace(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir() or path == Path(path.anchor):
        raise argparse.ArgumentTypeError("workspace must be an existing non-root directory")
    return path


def _safe_text(value: str, limit: int = 1200) -> str:
    return sanitize_text(value, limit=limit)


def _safe_list(values: list[str] | None, *, limit: int = 20) -> list[str]:
    return [_safe_text(value, 500) for value in (values or [])[:limit]]


def _relative_path(workspace: Path, value: str | None) -> str | None:
    if value is None:
        return None
    candidate = (workspace / value).resolve()
    if not candidate.is_relative_to(workspace):
        raise ValueError(f"path must stay inside workspace: {value}")
    return candidate.relative_to(workspace).as_posix()


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- 无"


def _write_task_brief(workspace: Path, args: argparse.Namespace) -> Path:
    read_scope = [_relative_path(workspace, value) for value in (args.read_scope or [])]
    write_scope = [_relative_path(workspace, value) for value in (args.write_scope or [])]
    dependencies = list(args.depends_on or [])
    success = _safe_list(args.success)
    prohibitions = _safe_list(args.prohibition)
    common = (
        f'--workspace "{workspace}" --run-id {args.run_id} '
        f'--task-id {args.task_id} --agent {args.agent}'
    )
    script = Path(__file__).resolve()
    content = f"""# { _safe_text(args.title, 160) }

## 目标

{_safe_text(args.objective)}

## 依赖

{_bullets(dependencies)}

## 可读取范围

{_bullets([value for value in read_scope if value])}

## 可写入范围

{_bullets([value for value in write_scope if value])}

## 成功证据

{_bullets(success)}

## 禁止事项

{_bullets(prohibitions)}

## workflow.py progress

阶段发生实质变化时运行：

`python "{script}" progress {common} --phase <phase> --message <safe-summary> [--evidence <workspace-relative-path>]`

## workflow.py deliver

结束时仅运行一次：

`python "{script}" deliver {common} --status <completed|blocked|failed|aborted> --summary <safe-summary> [--file <workspace-relative-path>] [--test <result>] [--artifact <workspace-relative-path>] [--risk <risk>] [--unresolved <item>]`

仅报告可公开的结构化进度与证据；不要写入提示词、推理过程、密钥、Cookie 或原始工具输入输出。
"""
    brief = workspace / ".agent-coordination" / "runtime" / "briefs" / f"{args.task_id}-{args.agent}.md"
    _write_text(brief, content)
    return brief


def _write_handoff(
    workspace: Path,
    args: argparse.Namespace,
    *,
    files: list[str],
    tests: list[str],
    artifacts: list[str],
    risks: list[str],
    unresolved: list[str],
) -> Path:
    handoff = workspace / ".agent-coordination" / "handoffs" / f"{args.task_id}-{args.agent}.md"
    content = f"""# {args.task_id} / {args.agent} 交付

## 结论

{_safe_text(args.summary)}

## 文件

{_bullets(files)}

## 验证

{_bullets(tests)}

## 产物

{_bullets(artifacts)}

## 风险

{_bullets(risks)}

## 未决事项

{_bullets(unresolved)}
"""
    _write_text(handoff, content)
    return handoff


def _append_event(workspace: Path, event: dict[str, object]) -> Path:
    runtime = workspace / ".agent-coordination" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    ledger = runtime / "events.jsonl"
    if ledger.exists() and ledger.stat().st_size > _MAX_LEDGER_BYTES:
        raise RuntimeError("runtime event ledger exceeds 8 MiB")
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    lock_path = runtime / "events.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+b") as lock_stream:
        if lock_stream.read(1) == b"":
            lock_stream.write(b"0")
            lock_stream.flush()
        lock_stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            with ledger.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if os.name == "nt":
                lock_stream.seek(0)
                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
    return ledger


def _base_event(args: argparse.Namespace, *, kind: str, status: str, title: str, detail: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "id": f"evt-{uuid.uuid4().hex}",
        "run_id": args.run_id,
        "task_id": args.task_id,
        "agent": args.agent,
        "session_id": getattr(args, "session_id", None),
        "kind": kind,
        "status": status,
        "phase": _safe_text(args.phase, 80),
        "title": _safe_text(title, 160),
        "detail": _safe_text(detail),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence": [],
        "deliverables": {},
    }


def _run(args: argparse.Namespace) -> int:
    workspace = args.workspace
    brief: Path | None = None
    handoff_path: Path | None = None
    if args.command == "task":
        event = _base_event(
            args, kind="task", status="planned", title=args.title, detail=args.objective
        )
        event["dependencies"] = list(args.depends_on or [])
        brief = _write_task_brief(workspace, args)
    elif args.command == "progress":
        event = _base_event(
            args, kind="progress", status=args.status, title=f"Progress: {args.phase}", detail=args.message
        )
        event["evidence"] = _safe_list(args.evidence)
    else:
        args.phase = "delivered"
        event = _base_event(
            args, kind="delivery", status=args.status, title="Agent delivery", detail=args.summary
        )
        files = [_relative_path(workspace, value) for value in (args.file or [])]
        artifacts = [_relative_path(workspace, value) for value in (args.artifact or [])]
        delivered_files = [value for value in files if value]
        delivered_tests = _safe_list(args.test)
        delivered_artifacts = [value for value in artifacts if value]
        delivered_risks = _safe_list(args.risk)
        delivered_unresolved = _safe_list(args.unresolved)
        deliverables: dict[str, object] = {
            "files": delivered_files,
            "tests": delivered_tests,
            "artifacts": delivered_artifacts,
            "handoff": None,
            "risks": delivered_risks,
            "unresolved": delivered_unresolved,
        }
        if args.handoff:
            handoff = _relative_path(workspace, args.handoff)
        else:
            handoff_path = _write_handoff(
                workspace,
                args,
                files=delivered_files,
                tests=delivered_tests,
                artifacts=delivered_artifacts,
                risks=delivered_risks,
                unresolved=delivered_unresolved,
            )
            handoff = handoff_path.relative_to(workspace).as_posix()
        deliverables["handoff"] = handoff
        event["evidence"] = [handoff] if handoff else []
        event["deliverables"] = deliverables
    path = _append_event(workspace, event)
    result = {"event_id": event["id"], "ledger": str(path), "hidden": event["detail"] == HIDDEN_TEXT}
    if brief is not None:
        result["brief"] = str(brief)
    if args.command == "deliver" and handoff is not None:
        result["handoff"] = str(workspace / handoff)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=_workspace)
    parser.add_argument("--run-id", required=True, type=_identifier)
    parser.add_argument("--task-id", required=True, type=_identifier)
    parser.add_argument("--agent", required=True, choices=_AGENTS)
    parser.add_argument("--session-id", type=_identifier)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record evidence-backed multi-agent workflow events.")
    commands = parser.add_subparsers(dest="command", required=True)
    task = commands.add_parser("task", help="register a task")
    _common(task)
    task.add_argument("--title", required=True)
    task.add_argument("--objective", required=True)
    task.add_argument("--phase", required=True)
    task.add_argument("--depends-on", action="append", type=_identifier)
    task.add_argument("--read-scope", action="append")
    task.add_argument("--write-scope", action="append")
    task.add_argument("--success", action="append")
    task.add_argument("--prohibition", action="append")
    progress = commands.add_parser("progress", help="append a progress checkpoint")
    _common(progress)
    progress.add_argument("--phase", required=True)
    progress.add_argument("--message", required=True)
    progress.add_argument("--status", choices=_STATUSES, default="running")
    progress.add_argument("--evidence", action="append")
    delivery = commands.add_parser("deliver", help="append the final delivery")
    _common(delivery)
    delivery.add_argument("--status", choices=("completed", "blocked", "failed", "aborted"), required=True)
    delivery.add_argument("--summary", required=True)
    delivery.add_argument("--file", action="append")
    delivery.add_argument("--test", action="append")
    delivery.add_argument("--artifact", action="append")
    delivery.add_argument("--handoff")
    delivery.add_argument("--risk", action="append")
    delivery.add_argument("--unresolved", action="append")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(build_parser().parse_args(argv))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"workflow event rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
