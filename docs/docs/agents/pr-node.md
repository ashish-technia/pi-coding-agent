---
sidebar_position: 4
title: PR Node
---

# PR Node

**File:** `src/pi_jira_agent/graph/nodes/pr_node.py`

Runs only after the human chose **Create pull request** at the final gate and
`PR_CREATION_ENABLED=true`.

It loops over the run's selected repositories, in `repos.json` order, and for each one:

1. `git add -A && git commit` with the coding agent's commit message (skipped if the tree is clean).
2. `git push -u <remote> <branch>` for the plan's branch name — the **same name in every repo**.
3. `POST /2.0/repositories/{workspace}/{repo}/pullrequests` on Bitbucket, using that repository's
   own `bitbucket_repo_slug` and `target_branch`, with the **human-supplied title** (`pr_title`
   from the decision) and the description (edited in the dialog, else the coding agent's).

A repository with a clean tree that is also not ahead of its target branch is **skipped**, so a
repo the agent never touched does not get an empty pull request.

The result sets `pr_urls` (`{repo name: URL}`) and `status = done`. Choosing **Finish without a
PR** ends the run with the changes left in the local clones.

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

`JIRA_TRANSITION_DONE_ID`, when set, is applied once after the pull requests are created.
