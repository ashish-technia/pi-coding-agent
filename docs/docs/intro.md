---
sidebar_position: 1
title: Introduction
---

# Pi Jira Agent

**Pi Jira Agent** turns a Jira issue into a reviewed pull request, with a human approving every stage.

A run has six stages:

1. **Requirements** — the issue's summary, description and comments are framed into goals, acceptance criteria and scope. You edit and approve; edits that widen the scope are flagged.
2. **Plan** — a coding agent in read-only *plan mode* reads the repository (or [several](./getting-started/repositories.md), attached to one session) and produces per-file steps with evidence, verification commands and optional phases. Approve, refine (it answers your notes) or reject.
3. **Code** — the agent implements the approved plan, or one phase of it, with full tools.
4. **Review** — a reviewer model checks the diff against the acceptance criteria, the plan and your team's review rules, and sends it back for fixes when needed.
5. **Final review** — you inspect the complete diff.
6. **PR** — optionally commit, push and open the Bitbucket pull request with your title.

Two channels drive the same workflow:

| Channel | Trigger | Feedback |
|---|---|---|
| **UI (flow 1)** | enter an issue key | screens with Approve / Revise / Reject |
| **Jira comments (flow 2)** | label or assignment via Jira Automation | the agent's account posts a comment; humans reply `/approve`, `/revise …`, `/pr <title>` |

---

## Key properties

| Property | Detail |
|---|---|
| Durable pauses | LangGraph `StateGraph` with a Postgres (or SQLite) checkpointer; a run can wait days for a reply |
| Channel-agnostic decisions | every gate is an `interrupt()` with a typed payload; the UI and the Jira channel produce the same decision objects |
| True plan mode | Pi session restricted to `read`, `grep`, `find`, `ls`; paths are validated against the repo and against what was read |
| Phased delivery | the planner may split work into phases; you can implement and review one at a time |
| Per-stage models | requirements, planning, coding and review each have their own provider/model/key |
| Review rules | a Markdown file of must-check items fed to the reviewer |
| Scalable later | Redis job queue and Postgres let several workers share load |

## Quick links

- [Installation →](getting-started/installation)
- [Configuration →](getting-started/configuration)
- [Technical design (system, code flow, state, protocols) →](architecture/technical-design)
- [Architecture overview →](architecture/overview)
- [Agents →](agents/requirements-agent)
- [REST API →](api/endpoints)
- [Jira setup for flow 2 →](operations/webhook)
