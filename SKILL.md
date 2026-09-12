---
name: orchestrating-multi-agent-dag
description: Use when coordinating Codex, DSH, and WorkBuddy across a complex task, especially when parallel ownership, evidence-backed status, persistent handoffs, or a live DAG observer are required.
---

# Orchestrating Multi-Agent DAG

## Start safely

1. Read user/project rules, focused Mnemon memories, `.agent-coordination/README.md`, `status.md`, and `git status --short`.
2. If `.agent-coordination` is absent, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\skills\orchestrating-multi-agent-dag\scripts\init-coordination.ps1" -Workspace (Get-Location).Path
   ```

3. Do not overwrite an existing coordination directory. Treat all existing source changes as user-owned.

## Build the execution DAG

Give every unit a unique `task_id`, owner, objective, dependencies, read/write scope, prohibitions, success evidence, and handoff path. One file has one writer at a time. Parallelize only independent units; keep shared ports, profiles, build outputs, and integration steps serialized.

Use [roles and routing](references/roles-and-routing.md) before dispatching DSH or WorkBuddy. Use [task and handoff contract](references/task-and-handoff-contract.md) for every prompt. Use [observer and evidence contract](references/observer-evidence-contract.md) when rendering or reporting state.

## Execute and reconcile

- Codex coordinates, resolves dependencies, integrates, and performs final verification.
- DSH defaults to investigation, validation, or read-only review; grant writes only through an exact task scope.
- WorkBuddy handles bounded implementation, tests, or real-entry acceptance; grant writes only through an exact task scope.
- Record confirmed durable facts and failure lessons in Mnemon; never store secrets or short-lived noise.
- Require a self-contained handoff from each worker. Independently inspect files, commands, exit codes, tests, and the real entry point before marking `DONE`.

## Observe

From `.agent-coordination` run:

```powershell
.\observer\start-observer.ps1
```

The center canvas is the fixed A·DAG overview: task root → three Agents → three trace summaries → aggregation. Raw event parent chains belong only in the detail panel.

## Example

For `catalog-import-20260912`: assign DSH a read-only API-contract investigation, WorkBuddy exclusive write ownership of the importer and its tests, and Codex ownership of integration docs and final validation. Both branches depend on the root task; WorkBuddy implementation depends on DSH's reviewed contract. Each writes `handoffs/<task_id>-<owner>.md`; Codex updates `status.md` only after checking the evidence.

## Quick reference

| Need | Rule |
|---|---|
| DSH model | Verify `agent-default-model = workbuddy/deepseek-v4.1-flash`, effort `max`; Headless has no per-run model flags |
| WorkBuddy model | Pass `--model glm-5.3-flash --effort high` every run |
| Status | `accepted`/process exit/self-report are not completion |
| Missing linkage | Show `unknown`; never infer a parent/task |
| Existing files | Preserve by default; refresh observer only when explicitly intended |

## Common mistakes

- Do not give two Agents overlapping write scopes.
- Do not copy Web/Headless profiles, subscriptions, cookies, credentials, or unrelated Skills.
- Do not turn every raw event into a main-graph node.
- Do not invent progress percentages, ETA, model selection, or successful validation.
