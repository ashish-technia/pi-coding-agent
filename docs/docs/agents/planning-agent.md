---
sidebar_position: 1
title: Planning Agent
---

# Planning Agent

**File:** `src/pi_jira_agent/graph/nodes/planning_agent.py`

## Purpose

The planning agent reads a Jira issue and produces a structured implementation plan **without touching the repository**. This plan is shown to the human reviewer before any code is written.

## How it works

```python
repos = selected_repos(repo_map, state)          # the run's checked repositories
plan_result = await pi_agent.run_with_mode(
    issue,
    repo_cwd=workspaces.cwd(key, repos),              # primary repo's worktree = the session's cwd
    repo_roots=workspaces.roots_payload(key, repos),  # every attached worktree, by absolute path
    execute_changes=False,                       # plan mode: no file writes
)
```

It calls the Pi SDK in **plan mode** (`execute_changes=False`). In `node/pi-sdk-runner.mjs` this maps to a read-only tool allowlist for the Pi session:

```js
const PLAN_MODE_TOOLS = ["read", "grep", "find", "ls"];
```

So the planner can open files, grep, and list directories to ground its plan in real code, but it has no `bash`, `edit`, or `write` tool, so it cannot change anything. The coding agent is the only session that gets the full tool set.

When a run attaches [several repositories](../getting-started/repositories.md), all of them are readable in the one session, and the prompt lists each root with its absolute path and its configured `properties`. One plan therefore covers the whole change, which is the point: a contract and its caller get planned together instead of in ignorance of each other.

### The planning procedure

The plan prompt gives Pi a fixed procedure: orient (read the repo's README / build files), locate (grep for every identifier the issue names), read every file it intends to change in full and trace callers, check existing tests and docs, then choose the smallest change set. Reasoning effort is set by `PI_THINKING_LEVEL` (default `medium`).

### Structured plan output

Besides the PR metadata, the planner must return:

| Field | Meaning |
|---|---|
| `analysis` | What it found in the code and why the change is needed, citing files and symbols it read |
| `plan_steps[]` | One entry per file: `file`, `action` (`modify` / `create` / `delete`), `change` (the exact edit), `evidence` (what is currently at that spot), and `repo` when several are attached |
| `verification[]` | Runnable checks that prove the change works |
| `open_questions[]` | Ambiguities the reviewer must resolve |

### Validation against the repository

The runner does not trust the model's paths. After each plan it checks that every `modify` / `delete` step points at a file that exists **and** that the agent actually opened with the `read` tool during the session, that `create` steps do not clobber existing files, that every path resolves **inside one of the attached repositories**, and that `analysis`, `change`, `evidence`, and `verification` are non-empty. Failures are sent back to the same session as a correction prompt, up to two rounds; if the plan is still invalid the run fails with the list of problems, so a guessed plan never reaches the approval screen.

The approved plan is then passed verbatim to the coding agent as its work order, and to the review agent as the checklist the diff must satisfy.

Because the planner explores the repository, a planning call takes minutes rather than seconds. `PI_TIMEOUT_SECONDS` defaults to 600; raise it for large repos, and raise it again when a run attaches several.

## Inputs from state

| Field | Used for |
|---|---|
| `issue` | The Jira issue (key, summary, description) |
| `repos` | Which repositories to attach; the first is the primary |
| `plan_notes` | Reviewer refinement notes, which switch the runner to the refinement prompt |

## Refinement loop

If the human reviewer clicks **Refine** instead of Approve, they provide notes explaining what needs changing or asking a question. The next `planning_agent` call passes the previous `plan_result` and the notes to the runner (`plan` and `reviewerNotes` in the payload), which switches to a refinement prompt:

- the planner sees its previous plan and the reviewer's notes verbatim;
- it investigates only what the notes require, re-reading the files it plans to change (validation still insists every `modify` path was opened in this session);
- it must answer the notes explicitly in `notes_response`, shown in the UI as "Reply to your notes";
- it returns the complete revised plan, not a delta.

This is much cheaper than the first plan because the orientation and discovery work is not repeated.

After a successful re-plan, `plan_notes` is cleared back to `""` in state.

## Output state updates

| Field | Value |
|---|---|
| `plan_result` | `AgentResult` with the analysis, per-file steps, verification and optional phases |
| `phases_total` | Number of phases, or 1 |
| `status` | `"pending_plan"` |
| `plan_notes` | `""` (cleared) |
| `current_node` | `"planning_agent"` |

The full `AgentResult` and `PlanStep` shapes are in [Graph State](../architecture/state.md#agentresult-model).
