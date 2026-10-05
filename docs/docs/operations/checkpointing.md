---
sidebar_position: 1
title: Checkpointing & Restarts
---

# Checkpointing & Restarts

## How checkpoints work

Pi Jira Agent uses **`AsyncSqliteSaver`** from `langgraph-checkpoint-sqlite` to persist graph state after every node completes.

Runs interrupted by a restart are resumed automatically, once each; see [Retry](./retry.md#after-a-restart).

Each run is keyed by `thread_id = issue.key` (e.g. `HE-1234`). The SQLite file is at the path configured by `GRAPH_CHECKPOINT_DB` (default: `data/graph_checkpoints.sqlite`).

```
data/
└── graph_checkpoints.sqlite    ← git-ignored, created automatically
```

### What is stored

The full `GraphState` is serialized using `JsonPlusSerializer` with registered Pydantic model support for `JiraIssue` and `AgentResult`. This includes every field — issue, plan, code result, diff, review verdict, iteration counters, and `current_node`.

---

## Restart recovery behaviour

When the server restarts, `AutomationService.start()` re-opens the checkpoint DB. The in-process `_tasks` dict and `progress` dict are empty. On the next call to `process_issue()` or a status poll, the service reads the checkpoint:

| Checkpoint state | `is_running` | Action |
|---|---|---|
| No checkpoint | false | Fresh start |
| `next = ["await_approval"]` | false | Leave paused; UI shows approval panel |
| `next = ["coding_agent"]` (no error) | false | **Auto-resume** as background task |
| `next = ["review_agent"]` (has error) | false | Surface `stuck_error`; user clicks Retry |
| Terminal state (`done`/`failed`/`cancelled`) | false | Allow fresh re-run |

### Auto-resume

The auto-resume happens inside `process_issue()`:

```python
existing = await self.graph.aget_state(config)
if existing and existing.next:
    next_nodes = set(existing.next)
    if next_nodes <= _HUMAN_INTERRUPT_NODES:   # {"await_approval"}
        return await self.get_status(issue.key)  # paused for human
    error = self._first_task_error(existing)
    if not error:
        self._run_background(issue.key, self.graph.ainvoke(None, config=config))
    return await self.get_status(issue.key)
```

---

## Current node after restart

The `progress.py` module is in-process only. After a restart, `progress.get(issue_key)` returns `None`. The status endpoint falls back to `values["current_node"]` from the checkpoint — every node sets this field in its return dict, so the UI always shows the last step that ran.

---

## Manual checkpoint inspection

```bash
sqlite3 data/graph_checkpoints.sqlite
> .tables
> SELECT thread_id, type, step FROM checkpoints ORDER BY step DESC LIMIT 10;
```

---

## Clearing a run

To restart a run from scratch (e.g. during development), delete the checkpoint row:

```bash
sqlite3 data/graph_checkpoints.sqlite
> DELETE FROM checkpoints WHERE thread_id = 'HE-1234';
> DELETE FROM checkpoint_writes WHERE thread_id = 'HE-1234';
```

Then call `POST /run` again.
