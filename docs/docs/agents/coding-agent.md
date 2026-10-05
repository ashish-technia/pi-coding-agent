---
sidebar_position: 2
title: Coding Agent
---

# Coding Agent

**File:** `src/pi_jira_agent/graph/nodes/coding_agent.py`

## Purpose

The coding agent **executes** the approved plan by writing code in the run's own git worktree of each selected repository. It uses the Pi SDK in execute mode, which allows the AI to actually create, modify, and delete files.

## How it works

1. On its first pass, creates the plan's branch in the run's worktree of **every selected repository**
2. Calls Pi SDK in **execute mode** (`execute_changes=True`) once, with all of them attached
3. Captures a `git diff` per repository and stores them in state for the review agent

```python
repos = selected_repos(repo_map, state)

git_clients = {r.name: workspaces.git(key, r.name) for r in repos}   # one per worktree

if first_pass:
    for git_branch in git_clients.values():  # the same branch name in each repo
        git_branch.create_branch(plan_result.branch_name)

code_result = await pi_agent.run_with_mode(
    issue,
    repo_cwd=workspaces.cwd(key, repos),     # primary repo's worktree = bash's working directory
    repo_roots=workspaces.roots_payload(key, repos),
    execute_changes=True,
    branch_name=plan_result.branch_name,
    plan=work_order,                         # the current phase's slice
)

diffs = {name: git_branch.diff()
         for name, git_branch in git_clients.items() if git_branch.has_changes()}
```

**One session edits every repository.** Phases slice the plan by stage, not by repository, so both
sides of a cross-repo change land in the same pass and are reviewed together. `bash` still has a
single working directory — the primary repo — so the prompt tells the agent to `cd` to another
repository's absolute path before running its verification commands.

The approved `plan_result` is serialized into the runner payload and rendered into the execute-mode prompt as the agent's work order: its analysis, every `plan_steps` entry, and the verification list. The agent is told to re-read each file before editing, to adapt if the code differs from the plan's evidence, and to run the verification steps with `bash` (tests, linters, imports) and fix what they surface before returning. Because the worktree is a fresh checkout, the prompt also says that dependencies are not installed and that checks needing them should be skipped, not installed for.

The Pi call is wrapped in `slots.hold(...)` (`graph/slots.py`), so at most `MAX_CONCURRENT_RUNS` planning or coding sessions run at once.

## Review feedback loop

When retrying after a rejected review, the reviewer's comments are folded into the issue description before calling Pi SDK:

```
<original description>

Review feedback to address: <reviewer comments>
```

This gives the coding agent specific guidance on what to fix in the next attempt.

## Inputs from state

| Field | Used for |
|---|---|
| `issue` | Jira issue (modified with feedback if retrying) |
| `plan_result` | Branch name to create, and the full plan passed to Pi as the work order |
| `repos` | Which repositories' worktrees to branch, attach and diff |
| `review_feedback` | Appended to issue description on retry |
| `iteration` | Current loop count (used for logging) |

## Output state updates

| Field | Value |
|---|---|
| `code_result` | `AgentResult` — the final commit/PR metadata |
| `diffs` | `{repo name: git diff}`; repositories with no changes are absent |
| `status` | `"reviewing"` |
| `iteration` | Incremented by 1 |
| `current_node` | `"coding_agent"` |

## Worktree and branch

The node never edits the configured clone. `prepare_workspace` (the node before planning) fetched
each selected repository and created a detached worktree at `origin/<target_branch>` under
`<RUNS_ROOT>/<issue key>/<repo name>`; planning read that same commit. On the first coding pass the
node runs `git checkout -B <branch>` in each worktree to create the plan's branch (e.g.
`feature/HE-1234-...`) there.

A worktree that has gone missing (for example `RUNS_ROOT` was not on a volume and the container
restarted) makes the node raise, so the run shows `stuck_error`. It is never recreated empty,
because it held the run's uncommitted work.

## git diff capture

After Pi SDK completes, `GitBranchClient.diff()` runs `git diff HEAD` in each selected repository to
capture all changes relative to the last commit. Nothing is committed until the PR node, so each
worktree still holds every change made for the issue so far. A repository the agent never
touched is simply absent from `diffs`; if `diffs` is empty altogether the review agent rejects with
"no changes were made".
