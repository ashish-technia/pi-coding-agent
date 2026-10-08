---
sidebar_position: 2
title: Error Handling & Retry
---

# Error Handling & Retry

## Types of failures

### 1. Node exception (`stuck_error`)

A node raised an unhandled exception (LLM auth error, API timeout, subprocess crash). The graph halted and wrote an error to the checkpoint.

**UI shows:** red error box with the exception message and a **Retry** button.

**`GET /api/runs/{key}` response:**
```json
{
  "status": "stuck_error",
  "error": "AuthenticationError: invalid api key",
  "stuck_on": ["review_agent"]
}
```

**Recovery:** click Retry (or `POST /runs/{key}/retry`).

A run that reaches `RUN_BUDGET_USD` stops the same way, with an error naming the budget and what was
spent. Retrying it straight away hits the same check: raise the budget, restart the service, then
retry. See [Configuration](../getting-started/configuration.md#cost).

### 2. Review exhausted (`failed`)

The review agent rejected the diff `REVIEW_MAX_ITERATIONS` times. Terminal state.

**UI shows:** orange retry banner explaining the last feedback, then a failure result.

**Recovery:** fix the underlying issue (bad prompt, bad model config, unclear Jira issue) and start a fresh run via `POST /api/runs`.

### 3. Cancelled

Human clicked Quit on the approval screen. Terminal state. A fresh `POST /api/runs` will start a new run.

---

## Smart retry (`POST /runs/{key}/retry`)

`AutomationService.retry()` does more than a naive re-run:

```python
updates = {"retry_count": retry_count + 1}

if error and "review_agent" in stuck_nodes:
    # Inject the error message as context so the next attempt knows
    updates["review_feedback"] = (
        f"{existing_feedback}\n[Previous attempt errored: {error[:300]}]"
    )

await self.graph.aupdate_state(config, updates)
self._run_background(issue_key, self.graph.ainvoke(None, config=config))
```

## After a restart

When the service starts it looks through the run registry for runs that were cut short: a node was
due, no gate was waiting for an answer and no node had raised. Each such run is resumed from its
last checkpoint **once** (`auto_resumes` in state counts it). Runs waiting at a gate are left
exactly as they were, with the same `gate_id`.

If the run is interrupted a second time it is not resumed again, so a run that takes the process
down cannot do so in a loop. It then shows `stuck_error` with a message saying it was interrupted,
and `POST /api/runs/{key}/retry` continues it.

A coding pass that was interrupted leaves half an edit in the worktree. When `coding_agent` runs
again it first puts each worktree back to what the last finished pass left (its tree snapshot, or
the base commit before the first pass), so the agent does not build on a half-applied change. A
worktree that is gone is still an error, never recreated empty.

Key behaviours:

| Stuck node | What retry does |
|---|---|
| `review_agent` | Injects error message into `review_feedback`; next review attempt includes this context |
| `coding_agent` | Increments `retry_count`; re-runs the node from checkpoint as-is |
| `planning_agent` | Re-runs planning from checkpoint |
| `pr_review` | Runs the whole-change review again. With `skip_pr_review` it is skipped instead and the final gate says so |
| `pr_node` | Runs every repository again; each step is safe to repeat. A repository whose pull request already exists reuses it, so there is one per repository |
| `announce_node` | Posts the Jira comment only if it is not on the issue yet, then applies the transition. Bitbucket is not touched again |
| `prepare_workspace` | Fetches again and creates any worktree that is not there yet |

`retry_count` tracks error-based retries separately from `iteration` (review loop counter). Both are visible in `GET /api/runs/{key}`.

---

## Review retry loop

This is separate from error retries — it is the normal flow when the review agent rejects the diff:

```
coding_agent → review_agent → [rejected] → coding_agent → review_agent → ...
                                                            (up to max_iterations)
```

Each retry:
1. `review_feedback` from the previous rejection is appended to the issue description fed to the coding agent
2. The review agent receives the previous feedback in its prompt and explicitly checks whether it was addressed
3. `iteration` increments by 1 in coding_agent

When `iteration >= max_iterations`, the orchestrator routes to `failed_node`.

---

## Configuring retry limits

```env
REVIEW_MAX_ITERATIONS=2   # default; increase for complex issues
```

Setting this to `1` means: one coding attempt, one review; if rejected → fail immediately.
Setting to `3+` gives the coding agent more chances but costs more LLM tokens and time.
