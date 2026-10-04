---
sidebar_position: 2
title: Coding Agent
---

# Coding Agent

**File:** `src/pi_jira_agent/graph/nodes/coding_agent.py`

## Purpose

The coding agent **executes** the approved plan by writing code to the local repository. It uses the Pi SDK in execute mode, which allows the AI to actually create, modify, and delete files.

## How it works

1. Optionally prepares the source branch in **every selected repository** (`git fetch`, `git checkout`)
2. Calls Pi SDK in **execute mode** (`execute_changes=True`) once, with all of them attached
3. Captures a `git diff` per repository and stores them in state for the review agent

```python
repos = selected_repos(repo_map, state)

if prepare_branch_before_pr and first_pass:
    for repo in repos:                       # the same branch name in each repo
        git_clients[repo.name].prepare_branch(
            target_branch=repo.target_branch,
            source_branch=plan_result.branch_name,
            push=False,
        )

code_result = await pi_agent.run_with_mode(
    issue,
    repo_cwd=repos[0].path,                  # primary repo = bash's working directory
    repo_roots=repo_roots_payload(repos),
    execute_changes=True,
    branch_name=plan_result.branch_name,
    plan=work_order,                         # the current phase's slice
)

diffs = {r.name: git_clients[r.name].diff()
         for r in repos if git_clients[r.name].has_changes()}
```

**One session edits every repository.** Phases slice the plan by stage, not by repository, so both
sides of a cross-repo change land in the same pass and are reviewed together. `bash` still has a
single working directory — the primary repo — so the prompt tells the agent to `cd` to another
repository's absolute path before running its verification commands.

The approved `plan_result` is serialized into the runner payload and rendered into the execute-mode prompt as the agent's work order: its analysis, every `plan_steps` entry, and the verification list. The agent is told to re-read each file before editing, to adapt if the code differs from the plan's evidence, and to run the verification steps with `bash` (tests, linters, imports) and fix what they surface before returning.

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
| `plan_result` | Branch name for branch preparation, and the full plan passed to Pi as the work order |
| `repos` | Which repositories to prepare, attach and diff |
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

## Branch preparation

Controlled by `PREPARE_BRANCH_BEFORE_PR` (default: `true`). When enabled, before calling Pi SDK the agent:

1. Fetches the remote
2. Checks out that repository's own `target_branch` (e.g. `develop`) and pulls
3. Creates the source branch (e.g. `feature/HE-1234-...`) from it

It runs once, on the first coding pass, for each selected repository. Every repository must have a
local path configured, or the node raises.

## git diff capture

After Pi SDK completes, `GitBranchClient.diff()` runs `git diff HEAD` in each selected repository to
capture all changes relative to the last commit. Nothing is committed until the PR node, so each
working tree still holds every change made for the issue so far. A repository the agent never
touched is simply absent from `diffs`; if `diffs` is empty altogether the review agent rejects with
"no changes were made".
