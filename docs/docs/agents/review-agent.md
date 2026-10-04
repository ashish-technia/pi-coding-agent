---
sidebar_position: 3
title: Review Agent
---

# Review Agent

**File:** `src/pi_jira_agent/graph/nodes/review_agent.py`

## Purpose

Acts as the code reviewer for every coding pass. It never touches the repository; it judges the git diff.

## What it sees

- The approved requirement: problem, goals, acceptance criteria, out-of-scope list.
- The approved plan **for the current phase** (the same slice the coding agent received): per-file steps and the verification list.
- The team's review rules from `REVIEW_RULES_PATH` (Markdown, "Must" and "Should" sections).
- The diff, and on retries the previous feedback so it can verify it was addressed.

When a run spans several repositories the prompt carries **one labelled diff per repository**
(`Diff in repo 'web':` …) and the plan steps are shown repo-qualified. This is deliberate: judged
on its own, a call added in one repository looks like a reference to something that does not
exist, and the reviewer would reject correct code. The system prompt tells it to read the diffs
together for exactly that reason.

## Verdict

```python
class ReviewVerdict(BaseModel):
    approved: bool
    comments: str
    must_violations: list[str]
```

A verdict with any `must_violations` is treated as rejected even if `approved` is true. Feedback plus violations become `review_feedback`, which the coding agent receives verbatim on the next attempt.

## Routing

- approved → `phase_gate`
- rejected and `iteration < REVIEW_MAX_ITERATIONS` → `coding_agent`
- rejected and the limit is reached → `failed_node`

The counter is per phase, so a two-phase plan with `REVIEW_MAX_ITERATIONS=2` allows up to four coding passes in total.

## Model

`REVIEW_MODEL_PROVIDER` / `REVIEW_MODEL` / `REVIEW_API_KEY`; providers `openai`, `anthropic`, `google`.
