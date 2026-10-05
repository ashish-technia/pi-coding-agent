---
sidebar_position: 2
title: Graph
---

# The LangGraph workflow

Built in `graph/build.py`. Solid edges are fixed; dotted edges are routers in `graph/orchestrator.py`.

```mermaid
graph TD
  S([start]) --> fetch_issue --> requirements_agent --> await_requirements
  await_requirements -.->|approve| scope_check
  await_requirements -.->|revise| requirements_agent
  await_requirements -.->|cancel| cancelled_node
  scope_check -.->|out of scope, not acknowledged| await_requirements
  scope_check -.->|ok| prepare_workspace --> planning_agent --> await_plan
  await_plan -.->|approve all / phased| coding_agent
  await_plan -.->|refine| planning_agent
  await_plan -.->|reject| cancelled_node
  coding_agent --> review_agent
  review_agent -.->|approved| phase_gate
  review_agent -.->|rejected, iteration < max| coding_agent
  review_agent -.->|rejected, max reached| failed_node
  phase_gate -.->|continue (next phase)| coding_agent
  phase_gate -.->|last phase / stop| await_final
  await_final -.->|create_pr| pr_node --> announce_node --> E([end])
  await_final -.->|finish| E
  cancelled_node --> E
  failed_node --> E
```

## Gates and their decisions

| Gate (interrupt type) | Actions | Effect |
|---|---|---|
| `await_requirements` (`requirements_approval`) | `approve` (+ edited `requirements`, `acknowledge_scope`), `revise` (+ `notes`), `cancel` | approve stores the edited spec and runs the scope check; revise re-frames with notes |
| `await_plan` (`plan_approval`) | `approve` (+ `mode`: `all` \| `phased`), `refine` (+ `notes`), `reject` | phased mode implements one phase per coding pass |
| `phase_gate` (`phase_gate`) | `continue`, `stop` | only pauses in phased mode when phases remain |
| `await_final` (`final_review`) | `create_pr` (+ `pr_title`, `pr_description`), `finish` | `create_pr` is refused when `PR_CREATION_ENABLED=false` |

The decision models live in `graph/decisions.py`. The service validates a raw decision against the pending type before resuming, so a `create_pr` sent while a plan is pending is rejected with HTTP 400.

## Status values

`fetching`, `framing_requirements`, `pending_requirements`, `planning`, `pending_plan`, `coding`, `reviewing`, `pending_phase`, `pending_final`, `creating_pr`, `done`, `failed`, `cancelled`, plus `stuck_error` reported by the service when a node raised.

While a gate is paused the effective status is derived from the pending interrupt type, because a node's state update is only stored when it returns.

## Repositories

A run carries the repositories it may work in (`repos` in state, chosen by the UI checkboxes or the
API). `prepare_workspace` gives the run its own git worktree of each one, created from the target
branch, so runs never share a working tree. `planning_agent` and `coding_agent` attach all of
those worktrees to one Pi session; `review_agent`
sees one labelled diff per repo; `pr_node` opens one pull request per repo that changed, and `announce_node` then tells Jira. Phases
slice the plan by stage, not by repository. See [Repositories](../getting-started/repositories.md).

## Loop counters

- `iteration`: coding→review cycles within the current phase; reset to 0 on each new phase.
- `phase_index` / `phases_total`: current phase in phased mode.
- `retry_count`: error-based retries via `/retry`, independent of the review loop.
