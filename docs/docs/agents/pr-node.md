---
sidebar_position: 4
title: PR Node
---

# PR Node

**File:** `src/pi_jira_agent/graph/nodes/pr_node.py` (two nodes: `pr_node` and `announce_node`)

Runs only after the human chose **Create pull request** at the final gate and
`PR_CREATION_ENABLED=true`.

It loops over the run's selected repositories, in `repos.json` order, and in each one's worktree:

1. `git add -A && git commit` with the coding agent's commit message (skipped if the tree is clean).
2. `git push --force -u <remote> <branch>` for the plan's branch name — the **same name in every repo**.
   A run always starts from the target branch, so a branch an earlier run of the same issue left on
   the remote is overwritten, including any commits someone else pushed to it.
3. Looks for an open pull request from that branch into the target branch
   (`GET …/pullrequests?q=source.branch.name = "…" AND destination.branch.name = "…" AND state = "OPEN"`)
   and reuses it if there is one. Otherwise:
   `POST /2.0/repositories/{workspace}/{repo}/pullrequests` on Bitbucket, using that repository's
   own `bitbucket_repo_slug` and `target_branch`, with the **human-supplied title** (`pr_title`
   from the decision) and the description (edited in the dialog, else the coding agent's).

A repository with a clean tree that also has no commits on top of the commit its worktree was
created from (`base_shas` in state) is **skipped**, so a
repo the agent never touched does not get an empty pull request.

The result sets `pr_urls` (`{repo name: URL}`); `announce_node` then sets `status = done`. Choosing **Finish without a
PR** ends the run and discards the changes; the last diff stays in the checkpoint.

Either way the run's worktrees and its local branch are removed once the run has ended.

## Retrying

Every step is safe to repeat, so `POST /api/runs/{key}/retry` on a run stuck in `pr_node` runs all
repositories again: the commit is skipped when the tree is clean, the push is a force-push of the
same commit, and the lookup finds the pull request an earlier attempt opened. A failure on the
second repository therefore ends, after a retry, with exactly one pull request per repository. A
reused pull request keeps the title and description it was created with.

## Telling Jira: `announce_node`

The Jira comment and the optional `JIRA_TRANSITION_DONE_ID` transition run in their own node, after
`pr_urls` has been checkpointed. If Jira fails, the retry starts here and Bitbucket is not touched
again. Before commenting, the node reads the issue's comments and skips the post when the same text
is already there, so a failed transition does not produce a second comment.

## Multi-repo pull requests

When more than one repository is selected, each description gets a footer naming the repositories
involved, and the single Jira comment lists every URL:

```
Automation created 2 pull requests for this issue:
- web: https://bitbucket.org/…/pullrequests/41
- api: https://bitbucket.org/…/pullrequests/12
```

The footer names repositories rather than URLs because the other pull requests do not exist yet
while the first is being opened, and Bitbucket has no notion of a linked pull request set.
**They cannot merge atomically** — deciding the merge order stays a human job.

`JIRA_TRANSITION_DONE_ID`, when set, is applied by `announce_node` after the comment.
