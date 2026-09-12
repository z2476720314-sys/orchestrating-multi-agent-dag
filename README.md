# Orchestrating Multi-Agent DAG

A portable Codex skill for coordinating Codex, DSH, and WorkBuddy with explicit ownership, persistent handoffs, evidence-backed status, and a live A·DAG observer.

## Install

Clone or copy this repository to `skills/orchestrating-multi-agent-dag` under your Codex home directory, then invoke:

```text
$orchestrating-multi-agent-dag
```

The initializer creates `.agent-coordination` inside the selected workspace without overwriting an existing coordination directory. Observer refreshes require the explicit `-RefreshObserver` switch.

## Included

- Task cards, ownership rules, dependency routing, and handoff contracts.
- Default DSH and WorkBuddy routing with post-dispatch model verification.
- A loopback-only observer with SSE updates and a fixed 8-node, 9-edge A·DAG.
- Distinct animated neon edges, card-bound endpoints, and evidence detail views.
- Privacy allowlists and explicit exclusion of profiles, subscriptions, cookies, credentials, and unrelated skills.

See [SKILL.md](SKILL.md) for the workflow and the files under `references/` for the detailed contracts.
