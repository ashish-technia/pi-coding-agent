---
sidebar_position: 1
title: Overview
---

# Architecture overview

```
                 ┌──────────────── one FastAPI process ─────────────────┐
 Browser (SPA) ─►│ /api/runs, /api/runs/{key}/decision                   │
 Jira Automation►│ /webhooks/jira/trigger, /webhooks/jira/comment        │
                 │        │                                              │
                 │   job queue (memory | Redis)                          │
                 │        │                                              │
                 │   AutomationService  ── run registry (runs table)     │
                 │        │                                              │
                 │   LangGraph StateGraph ── checkpointer (Postgres|SQLite)
                 │     fetch → requirements → [gate] → scope check       │
                 │     → planning → [gate] → coding ⇄ review → [phase gate]
                 │     → [final gate] → pr                               │
                 └───────┬──────────────┬──────────────┬─────────────────┘
                         │              │              │
                 node pi-sdk-runner   chat models    git / Bitbucket / Jira
                 (Pi harness)         (LangChain)    (httpx, subprocess)
```

## Layers

**API** ([`main.py`](https://github.com/)): FastAPI routes for the UI and for Jira Automation web requests. Serves the built React SPA from `static/dist`.

**Queue** (`queue_worker.py`): `InMemoryJobQueue` or `RedisJobQueue` behind one interface. Jobs are `{"kind": "start" | "comment", ...}`.

**Service** (`service.py`): owns the compiled graph and the checkpointer, starts runs as background tasks keyed by issue key, validates decisions against the pending gate, and after every pause updates the run registry and, in flow 2, posts the Jira comment.

**Graph** (`graph/`): nodes (`nodes/`), human gates (`nodes/gates.py`), routers (`orchestrator.py`), decision contracts (`decisions.py`), state (`state.py`).

**Channels** (`channels/jira_comments.py`): renders a pending gate as a Jira comment with the reply commands and maps human replies back to decisions.

**Pi bridge** (`pi_agent.py` + `node/pi-sdk-runner.mjs`): one `PiAgentExecutor` per stage (planning, coding), so each can use a different provider. The Node runner is the only place Pi is invoked; it enforces plan mode, validates plans, and returns the `AgentResult` JSON contract.

## Why LangGraph interrupts

Every human pause is a LangGraph `interrupt(payload)`. The payload carries a `type` (`requirements_approval`, `plan_approval`, `phase_gate`, `final_review`). The service exposes that payload as `pending` in the status, and `submit_decision` resumes the graph with `Command(resume=decision)` after validating the decision against the type. Because the checkpoint holds everything, a reply can arrive from a different process days later.

## Concurrency and scaling

- One asyncio task per running issue; several issues can run in parallel in one process.
- Each run works in its own git worktree per repository (`RUNS_ROOT`), so runs against the *same* repository do not interfere. The configured clones are only fetched.
- With `DATABASE_URL` and `REDIS_URL` set, multiple API/worker processes can share the queue and checkpoints.
