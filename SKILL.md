---
name: orchestrating-multi-agent-dag
description: Use when coordinating Codex, DSH, and WorkBuddy across a complex task, especially when parallel ownership, evidence-backed status, persistent handoffs, or a live DAG observer are required.
---

# Orchestrating Multi-Agent DAG

## Start before dispatch

Run this once from the target workspace. It installs or refreshes the observer, starts or reuses its hidden process, verifies health, and opens the live DAG page by default:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\skills\orchestrating-multi-agent-dag\scripts\start-workflow.ps1" -Workspace (Get-Location).Path
```

Use `-NoBrowser` only when the user explicitly does not want the page opened.

## Model judgment

The model decides only:

- task boundaries and dependencies;
- the single owner and non-overlapping read/write scope;
- success evidence, prohibitions, and whether work is ready to integrate;
- honest `blocked`/`failed` states when evidence is insufficient.

Keep shared files, ports, profiles, build outputs, and integration steps serialized. Never infer progress, percentages, ETA, or completion.

## Deterministic workflow

Use `scripts\workflow.py task --help` to register each task before dispatch. The command writes a task brief under `.agent-coordination/runtime/briefs/`; give that file to the assigned Agent and require it to follow the embedded commands.

Agents record a `progress` event at each meaningful phase change and exactly one `deliver` event when they stop. The scripts sanitize content, append the runtime ledger, and create the handoff; do not hand-format those files.

The observer merges this ledger with native lifecycle signals and streams updates through SSE. The fixed A·DAG remains the overview; select an Agent/task to see “实时进展” and “交付内容”.

## Completion gate

The coordinator independently checks changed files, tests, artifacts, handoff, exit status, and the real user-facing entry point. A process exit or Agent self-report alone is not completion.

Never record prompts, private reasoning, credentials, cookies, or raw tool input/output. For unusual DSH/WorkBuddy routing only, consult `references/roles-and-routing.md`.
