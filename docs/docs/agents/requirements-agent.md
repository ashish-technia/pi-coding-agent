---
sidebar_position: 0
title: Requirements Agent
---

# Requirements Agent

**File:** `src/pi_jira_agent/graph/nodes/requirements_agent.py`

## Purpose

Before anything is planned, the issue is turned into a precise requirement the human signs off. This is the contract every later stage works against: the planner must satisfy each acceptance criterion and stay out of the out-of-scope list, and the reviewer checks the diff against the same criteria.

## Inputs

The full Jira issue as fetched by `JiraClient.get_issue`: summary, description (Atlassian Document Format converted to text) and **every comment** with author and date. Comments often refine or override the description, so the prompt tells the analyst that later comments win and the reporter's statements outrank speculation.

## Output: `RequirementsSpec`

| Field | Meaning |
|---|---|
| `title`, `problem` | what is wrong or missing, in the user's terms |
| `goals` | outcomes the change must achieve |
| `acceptance_criteria` | observable, testable statements of done |
| `in_scope`, `out_of_scope` | explicit boundaries; anything mentioned but not requested goes out of scope |
| `assumptions` | what the analyst had to assume |
| `open_questions` | things only the reporter can settle |
| `sources` | where each item came from (description, comment by X, inferred) |

## Human review and the scope check

The UI shows the spec as editable lists. On approve, the (possibly edited) spec is compared with the analyst's original by a second structured call, `make_scope_check`:

- additions the issue never asked for → `out_of_scope`
- rewording, clarification, narrowing → `in_scope`
- ambiguous → `unclear`

If any item is `out_of_scope` and the human has not acknowledged it, the run returns to the requirements gate with the findings. The UI offers **Remove out-of-scope items** (strips the flagged strings from every list) or **Keep them anyway and continue** (`acknowledge_scope: true`). The scope check is skipped entirely when the spec was not edited.

**Revise** sends free-text notes; the analyst gets its previous framing plus the notes and returns a complete revised spec.

## Model

`REQUIREMENTS_MODEL_PROVIDER` / `REQUIREMENTS_MODEL` / `REQUIREMENTS_API_KEY`, falling back to the review model settings.
