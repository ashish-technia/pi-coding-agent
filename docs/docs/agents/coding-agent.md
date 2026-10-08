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

**Edits are held to the attached repositories.** Before any tool runs, the runner's pre-tool hook
checks it: an `edit` or `write` outside every attached repository is refused and never happens, and
a `bash` command that pushes, commits, changes remotes or calls a network client is refused too.
The agent sees the refusal as a failed tool call and continues. The shell check is a short
denylist, not containment; see [the runner protocol](../architecture/technical-design.md#9-the-pi-runner-protocol).

The approved `plan_result` is serialized into the runner payload and rendered into the execute-mode prompt as the agent's work order: its analysis, every `plan_steps` entry, and the verification list. The agent is told to re-read each file before editing, to adapt if the code differs from the plan's evidence, and to run the verification steps with `bash` (tests, linters, imports) and fix what they surface before returning. Because the worktree is a fresh checkout, the prompt also says that dependencies are not installed and that checks needing them should be skipped, not installed for.

The runner is started in its own process group with a minimal environment (see [Configuration](../getting-started/configuration.md)). When a call exceeds `PI_TIMEOUT_SECONDS`, or the service shuts down mid-session, the whole process tree is killed, including anything the agent's `bash` started (`killpg` on Linux, `taskkill /T /F` on Windows).

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
| `diffs` | `{repo name: git diff}` of the whole change so far; repositories with no changes are absent |
| `phase_diff` | `{repo name: git diff}` of what the current phase added; this is what the reviewer gets |
| `tree_shas` | `{repo name: tree SHA}` of each worktree after this pass |
| `status` | `"reviewing"` |
| `iteration` | Incremented by 1 |
| `current_node` | `"coding_agent"` |

## Leftovers of an interrupted pass

Before anything else the node compares each worktree with what the last finished coding pass left
(`tree_shas`, or the base commit before the first pass). If they differ, a previous pass was cut
short by a restart, and the worktree is put back with `GitBranchClient.restore()` so the agent
starts from a known state. On a normal pass the two are equal and nothing happens.

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

Each pass takes one tree snapshot per repository and makes two diffs from it:

- `diffs`: base commit → snapshot. The whole change so far, shown to the human at the gates.
- `phase_diff`: the tree the current phase started from → snapshot. Only this phase's work, shown
  to the reviewer. In the first phase, and in "all at once" mode, the two are the same.

When the human continues at the phase gate, the gate copies `tree_shas` into `phase_base`, so the
next phase is diffed against the tree this one ended on. Review retries inside a phase keep the
same `phase_base`, so the reviewer sees the phase's work as it now stands. The snapshot is kept
alive by a ref, `refs/pi-jira/<issue key>/phase-<n>`, which is deleted with the worktree when the
run ends.

After Pi SDK completes, `GitBranchClient.diff()` captures every change in each selected
repository's worktree, **including files the agent created**. It does not use `git diff HEAD`,
which leaves untracked files out. `snapshot()` copies the index to a temporary file, runs
`git add -A` and `git write-tree` against that copy, and the diff is taken between the commit the
worktree was created from (`base_shas`) and the resulting tree. The real index is never touched,
and `.gitignore` is respected. Nothing is committed until the PR node, so each worktree still holds
every change made for the issue so far. A repository the agent never
touched is simply absent from `diffs`; if `diffs` is empty altogether the review agent rejects with
"no changes were made".
