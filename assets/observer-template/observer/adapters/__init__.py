"""Source adapters and normalized snapshot collection."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..model import AdapterResult, ObserverConfig, Snapshot, SourceHealth
from .codex import CodexSource
from .coordination import CoordinationSource
from .dsh import DshSource
from .runtime import RuntimeSource
from .workbuddy import WorkBuddySource


def collect_snapshot(config: ObserverConfig) -> Snapshot:
    """Collect each read-only source once and merge normalized records."""

    results: list[AdapterResult] = []
    for source_type in (CoordinationSource, RuntimeSource, CodexSource, DshSource, WorkBuddySource):
        source_name = source_type.__name__.removesuffix("Source").casefold()
        try:
            results.append(source_type(config).collect())
        except Exception:  # noqa: BLE001 - one source must not stop the snapshot
            results.append(
                AdapterResult(
                    health=SourceHealth(
                        source=source_name,
                        status="degraded",
                        detail=f"{source_name} 采集异常",
                    )
                )
            )
    now = config.clock() if config.clock else time.time()
    generated_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
    return Snapshot(
        generated_at=generated_at,
        workspace=str(config.workspace),
        connection=(
            "degraded"
            if any(result.health.status == "degraded" for result in results)
            else "live"
        ),
        agents=tuple(agent for result in results for agent in result.agents),
        tasks=tuple(task for result in results for task in result.tasks),
        events=tuple(event for result in results for event in result.events),
        source_health=tuple(result.health for result in results),
    )


__all__ = [
    "CodexSource",
    "CoordinationSource",
    "DshSource",
    "RuntimeSource",
    "WorkBuddySource",
    "collect_snapshot",
]
