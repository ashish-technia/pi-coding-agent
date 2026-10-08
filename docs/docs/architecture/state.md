---
sidebar_position: 3
title: Graph State
---

# Graph State

`GraphState` is a `TypedDict` (all fields optional via `total=False`) that flows through every
node and is fully serialized to the checkpoint after each step. It mirrors
`src/pi_jira_agent/graph/state.py`.

```python
class GraphState(TypedDict, total=False):
    # Input
    issue_key: str
    issue: JiraIssue                  # fetched (or supplied inline)
    channel: Channel                  # "ui" | "jira" - where pauses are announced
    repos: list[str]                  # repo names this run works in; [0] is the primary
    base_shas: dict[str, str]         # repo name -> commit the run's worktree was created from

    # Requirements stage
    requirements_original: RequirementsSpec   # the agent's framing, kept for comparison
    requirements: RequirementsSpec            # current version, may include human edits
    requirements_notes: str                   # human "revise" notes for the next pass
    scope_check: ScopeCheck | None            # findings for the latest human edit
    scope_acknowledged: bool                  # human kept flagged out-of-scope items

    # Plan stage
    plan_result: AgentResult
    plan_notes: str                   # human "refine" notes for the next planning pass
    execution_mode: ExecutionMode     # "all" | "phased"
    phase_index: int                  # 0-based phase being implemented
    phases_total: int

    # Coding / review loop
    code_result: AgentResult
    diffs: dict[str, str]             # repo name -> working-tree diff; untouched repos absent
    phase_diff: dict[str, str]        # what the current phase changed, per repo; what the reviewer sees
    phase_diffs: list[dict[str, str]] # one phase_diff per accepted phase
    tree_shas: dict[str, str]         # tree snapshot of each worktree after the last coding pass
    phase_base: dict[str, str]        # tree the current phase started from; empty in the first phase
    review_approved: bool
    review_feedback: str
    review_omitted_files: list[str]   # changed files the last review did not see (diff over the limit)
    iteration: int                    # coding->review cycles within the current phase
    max_iterations: int               # from REVIEW_MAX_ITERATIONS

    # Delivery
    pr_title: str
    pr_urls: dict[str, str]           # repo name -> PR URL, one per repo that had changes

    # Bookkeeping
    status: Status
    current_node: str | None          # persisted so restarts can show where the run is
    retry_count: int                  # error-based retries, separate from the review loop
    error: str | None
```

## Why repos are stored by name

`repos` holds names, not paths or config objects. Checkpoints then stay small and survive edits
to `repos.json`: the `RepoConfig` registry and the git/Bitbucket clients are rebuilt at startup
in `graph/build.py`, and each node looks up what it needs by name through
`graph/repo_context.py`. A name that has disappeared from the config is skipped rather than
killing an in-flight run.

`decision_log` (bookkeeping) gets one entry per answered gate: `gate`, `action`, `by` and `at`. An entry
of the final gate on a run with a PR review also has `review`: the findings `shown` (number, severity,
category), the numbers sent back (`fix`) and the numbers `left`. `by` is set
by the service, `ui` or `jira:<accountId>`, never taken from the request.

The PR review has six fields. `pr_review_enabled` is chosen when the run starts and never changes.
`pr_review` holds the latest `ReviewResult` (`None` before it ran, or when it was skipped).
`pr_review_skipped` records that the review failed and the run was continued without it;
`pr_review_skip` is the request to do so, set by a retry and consumed by the node. `fix_request`
(`findings`, `notes`, `feedback`) is set by the final gate's `fix` action and cleared by the next
review; while it is set, `coding_agent` runs a fix pass and routes to `pr_review`. `fix_rounds`
counts those passes.

`manifest` (bookkeeping) records what produced the run. `manifest.build` takes it once, when the run
starts, and nothing changes it afterwards:

| Key | What it holds |
|---|---|
| `agent_version`, `agent_git_sha` | this service: package version and the commit it runs from (`AGENT_GIT_SHA` in an image, `git rev-parse HEAD` in a checkout) |
| `pi_sdk_version` | the installed Pi SDK, which also fixes the model catalogue |
| `runner_sha` | one hash over `node/pi-sdk-runner.mjs` and `node/review-mode.mjs`; the Pi prompts are built in those files |
| `stages` | provider and model per stage, the Pi thinking level where there is one, and whether PR review is on |
| `prompts` | version and hash of every file in `src/pi_jira_agent/prompts/`, and the hash of `PI_SYSTEM_PROMPT` |
| `rules` | hashes of the review rules and the PR review rules |
| `pack` | `builtin` until packs exist (R-30) |

Two runs with the same manifest came from the same system. It holds no key, token or path.

`usage` (bookkeeping) is the run's cost ledger: one entry per model call that returned, with `stage`,
`provider`, `model`, `input`, `output`, `cache_read`, `cache_write`, `cost_usd` and `at`. There is no
reducer on it, so a node returns the whole list with its entry added (`usage.appended`). `cost_usd` is
`None` for a call whose model has no price. `usage.check_budget` reads it before every model call.

`auto_resumes` (bookkeeping, next to `retry_count`) counts how often the service resumed the run by
itself after a restart; it stops at one.

### Starting state

`initial_state()` in `graph/state.py` builds the input for a new run and gives **every** field a
value. That matters on a restart: a run's checkpoint thread is its issue key, so starting a
finished issue again reuses the thread, and LangGraph keeps any key the new input leaves out.
Without the full set, the previous run's plan, scope findings, diffs or PR URLs would show up in
the new run. A new `GraphState` field needs a default there; `tests/test_state.py` fails otherwise.

Everything downstream of the selection is keyed the same way — `diffs`, `phase_diffs` and
`pr_urls` are all maps from repo name — so a single-repo run is simply a map with one entry.

---

## Status values

| Value | Meaning |
|---|---|
| `fetching` | Loading the Jira issue |
| `framing_requirements` | The requirements agent is writing the spec |
| `pending_requirements` | Waiting for the human to approve/revise the requirements |
| `planning` | The planning agent is reading the repositories |
| `pending_plan` | Waiting for the human to approve/refine the plan |
| `coding` | The coding agent is editing the repositories |
| `reviewing` | The review agent is judging the diffs |
| `pending_phase` | A phase passed review; waiting for continue/stop |
| `pending_final` | All phases done; waiting for the final decision |
| `creating_pr` | Committing, pushing and opening pull requests |
| `done` | Terminal success (PRs created, or finished without one) |
| `failed` | Review exhausted `max_iterations`; terminal failure |
| `cancelled` | The human cancelled or rejected; terminal |

The status endpoint also returns two synthetic statuses not stored in state:

| Synthetic status | Meaning |
|---|---|
| `not_started` | No checkpoint exists for this issue key |
| `stuck_error` | The graph has `next` nodes but is not running — a node raised an exception |

While a gate is paused the effective status is derived from the pending interrupt type, because
a node's state update is only stored when the node returns.

---

## AgentResult model

Both `plan_result` and `code_result` are `AgentResult` instances:

```python
class PlanStep(BaseModel):
    file: str                 # path relative to its repository's root
    action: Literal["modify", "create", "delete"] = "modify"
    change: str               # the exact edit to make
    evidence: str = ""        # what is currently in the file at that spot
    repo: str = ""            # which repository; empty on single-repo runs

class AgentResult(BaseModel):
    branch_name: str          # e.g. "feature/HE-1234-fix-login" - the same in every repo
    commit_message: str
    pr_title: str
    pr_description: str       # PR body (Markdown)
    files_changed: list[str]  # "repo-name/path" when the change spans repos, else "path"
    analysis: str = ""        # code-grounded findings (planner)
    plan_steps: list[PlanStep] = []
    verification: list[str] = []
    open_questions: list[str] = []
    notes_response: str = ""  # planner's reply to refinement notes
    phases: list[PlanPhase] = []
```

`plan_result` carries the structured plan the human approves; the same object is handed to the
coding agent as its work order and to the review agent as its checklist. `code_result` uses the
same shape to report what was actually done.

Anything stored in the state must be listed in `make_serde()` in `graph/build.py`, or the
checkpoint fails to deserialize.

---

## current_node — restart recovery field

Every node sets `current_node` in its return dict:

```python
return {
    "plan_result": plan_result,
    "status": "pending_plan",
    "current_node": "planning_agent",   # persisted to checkpoint
}
```

`get_status()` in `service.py` prefers the **in-process** `progress` dict (updated at the start
of each node, before the checkpoint write) for real-time granularity. After a server restart the
`progress` dict is empty, so it falls back to `values["current_node"]` from the checkpoint — this
way the UI always shows which step was last running.
