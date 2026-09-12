"""Command-line entry point for the local multi-agent observer."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TextIO, cast

from observer import ObserverConfig, collect_snapshot
from observer.engine import ObserverEngine
from observer.server import DEFAULT_PORT, LOOPBACK_HOST, create_server

VERSION = "1.1.2"
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]


class RuntimeEngine(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class RuntimeServer(Protocol):
    server_address: tuple[object, ...]

    def serve_forever(self) -> None: ...

    def server_close(self) -> None: ...


ServerFactory = Callable[..., RuntimeServer]
DEFAULT_SERVER_FACTORY = cast(ServerFactory, create_server)


def _loopback_host(value: str) -> str:
    if value != LOOPBACK_HOST:
        raise argparse.ArgumentTypeError(
            f"observer 只允许监听 {LOOPBACK_HOST}"
        )
    return value


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 0 到 65535 之间")
    return port


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("轮询间隔必须是数字") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("轮询间隔必须大于 0")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读汇总 Codex、DSH、WorkBuddy 与协调状态。"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help=f"共享工作区（默认：{DEFAULT_WORKSPACE}）",
    )
    parser.add_argument(
        "--host",
        type=_loopback_host,
        default=LOOPBACK_HOST,
        help=f"监听地址，仅允许 {LOOPBACK_HOST}",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"监听端口（默认：{DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--poll-seconds",
        type=_positive_seconds,
        default=1.0,
        help="采集间隔秒数（默认：1.0）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="采集一次 JSON 快照并退出，不启动 HTTP 服务",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _snapshot_dict(snapshot: object) -> dict[str, object]:
    if isinstance(snapshot, dict):
        return snapshot
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("snapshot must be a dict or provide to_dict()")
    payload = to_dict()
    if not isinstance(payload, dict):
        raise TypeError("snapshot.to_dict() must return a dict")
    return payload


def main(
    argv: Sequence[str] | None = None,
    *,
    collector: Callable[[ObserverConfig], object] = collect_snapshot,
    engine_factory: Callable[..., RuntimeEngine] = ObserverEngine,
    server_factory: ServerFactory = DEFAULT_SERVER_FACTORY,
    stdout: TextIO | None = None,
) -> int:
    args = parse_args(argv)
    output = stdout or sys.stdout
    config = ObserverConfig(workspace=args.workspace.resolve())

    if args.once:
        payload = _snapshot_dict(collector(config))
        json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
        output.write("\n")
        output.flush()
        return 0

    engine = engine_factory(config, poll_seconds=args.poll_seconds)
    server_config = SimpleNamespace(host=args.host, port=args.port)
    server = server_factory(server_config, engine)
    try:
        engine.start()
        host, port = server.server_address[:2]
        print(f"Observer 已启动：http://{host}:{port}/", file=output, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        finally:
            engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
