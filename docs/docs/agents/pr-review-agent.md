---
sidebar_position: 4
title: PR Review Agent
---

# PR Review Agent

**Files:** `src/pi_jira_agent/graph/nodes/pr_review.py`, `node/review-mode.mjs`

The PR review agent reviews the **whole change** once, after the last phase is accepted and
before the final gate. It is advisory: its findings are shown to the human at the final gate,
who decides whether to send some back, open the pull request anyway, or finish.

It is a different job from the [review agent](./review-agent.md):

| | Review agent | PR review agent |
|---|---|---|
| Runs | after every coding pass | once, before the final gate (and again after each fix round) |
| Sees | one phase's diff | everything the run changed, in every repository |
| Judges against | that phase's plan steps | the approved requirement and its acceptance criteria |
| How | one model call over a capped diff | a read-only Pi session that opens files itself |
| Effect | rejects into the coding loop | findings at the final gate; a human decides |

## Switching it on

The start screen has a switch, ticked or not according to `PR_REVIEW_DEFAULT`. A run started
from Jira has no start screen and always uses `PR_REVIEW_DEFAULT`. The choice is stored in the
run (`pr_review_enabled`) and cannot be changed afterwards. When it is off, the router goes
straight from the last phase gate to the final gate.

## What the session does

The node calls `PiAgentExecutor.run_review`, which runs the runner in
[review mode](../architecture/technical-design.md#9-the-pi-runner-protocol). The session:

- has `read`, `grep`, `find`, `ls`, and two tools of its own, `changed_files` and `file_diff`.
  There is no shell and no diff size cap; a single file's diff is cut at 60,000 characters.
- is held to the run's repositories by the pre-tool check, like plan mode.
- is told to find, for every acceptance criterion, the code that satisfies it; to check that
  the change is consistent across phases and repositories; to look for bugs and risky edits;
  and to apply the team rules (`PR_REVIEW_RULES_PATH`, or `REVIEW_RULES_PATH` when unset).
- takes one of the `MAX_CONCURRENT_RUNS` slots while it runs, and counts against
  `RUN_BUDGET_USD` as stage `pr_review`.

The diff it reviews is each repository's base commit against the tree snapshot of the last
coding pass, the same pair the human's diff at the final gate comes from.

## What comes back

A `ReviewResult`, stored in `GraphState["pr_review"]`:

- `summary` and `findings[]`. Each finding has a `number`, a `severity` (`must`, `should`,
  `note`), a `category`, a `claim`, a `suggestion`, and the `file` and `line` it is about.
  Findings are numbered by severity, so 1 is the most serious.
- `not_reviewed[]`: changed files the session never opened. Lockfiles and generated files are
  exempt. The final gate lists them.
- `dropped_findings[]`: findings the runner discarded because they cited a file the session
  had not opened. The gate shows the count.
- `resolved[]`: on a re-review, the numbers of the earlier findings that are now fixed.

The only finding allowed without a file is category `unmet_criterion`: an acceptance
criterion that nothing implements has no line to cite.

## The fix loop

At the final gate the human may answer `fix` with the numbers of the findings to send back and
optional notes of their own. With no numbers, every `must` finding is sent.

```
await_final --fix--> coding_agent --> pr_review --> await_final
```

- The coding agent gets the chosen findings and the notes as its feedback, and the whole plan
  as context rather than one phase of it.
- The phase reviewer is skipped. It judges a diff against a phase's plan steps and would call
  a fix an unrequested change.
- The PR review runs again as a re-review: it first checks each finding it was asked about,
  then reviews the rest of the change.
- `PR_REVIEW_MAX_FIX_ROUNDS` (default 2) caps the rounds. After that the gate still shows the
  findings but only offers the pull request or finishing. A run never fails because of fix
  rounds.
- `fix` is refused when the run has no PR review, or when the review did not run.

From Jira: `/fix` sends every `must` finding, `/fix 1 3` sends those two, and any text after
the numbers is the note.

## After the decision

- **Pull request.** `must` findings still open when the human chooses `create_pr` are added to
  the PR description under "Known review findings", so the Bitbucket reviewer sees what the
  agent flagged and the human accepted. `should` and `note` findings are not added.
- **Decision log.** Every answer at the final gate records what the review had shown, what
  was sent back and what was left (`decision_log[].review`). This is the data R-20 will use
  to measure the reviewer against human verdicts.

## When the review fails

A failed session (model error, timeout, budget) leaves the run as `stuck_error` on
`pr_review`, like any node. Retry runs it again. Retry can also be told to skip it
(`{"skip_pr_review": true}`, or "Continue without the PR review" in the UI), so a model outage
cannot hold a finished change hostage. The final gate then states that the change was not
reviewed, and `fix` is not offered.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `PR_REVIEW_DEFAULT` | `true` | on or off when nobody chose |
| `PR_REVIEW_MODEL_PROVIDER`, `PR_REVIEW_MODEL`, `PR_REVIEW_API_KEY` | the `REVIEW_*` values | the session's model. It falls back to the review model, never the coder's |
| `PR_REVIEW_THINKING_LEVEL` | `PI_THINKING_LEVEL` | Pi reasoning effort |
| `PR_REVIEW_RULES_PATH` | `REVIEW_RULES_PATH` | Markdown rules the review applies |
| `PR_REVIEW_MAX_FIX_ROUNDS` | `2` | fix rounds per run; `0` removes the fix action |

The model runs through Pi, so its id must be one Pi's catalogue knows. An unknown id fails the
first review with the runner's "Unknown model" error; it is not checked at startup.
