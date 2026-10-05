"""Flow 2 feedback channel: every pause becomes a Jira comment with reply commands.

The graph itself is channel-agnostic. This module only renders the pending
interrupt as a comment and maps human replies back into decisions.
"""

from __future__ import annotations

import logging
from typing import cast

from ..graph.decisions import command_help, parse_comment_command
from ..jira_client import JiraClient
from ..models import JiraIssue

logger = logging.getLogger(__name__)


def _partial_review_note(interrupt_value: dict) -> list[str]:
    omitted = interrupt_value.get("review_omitted_files") or []
    if not omitted:
        return []
    return [f"The review saw a partial diff. Not reviewed (too large): {', '.join(omitted)}"]


def render_pending(interrupt_value: dict) -> str:
    kind = interrupt_value.get("type")
    if kind == "requirements_approval":
        req = interrupt_value.get("requirements") or {}
        lines = [
            "I framed the requirement for this issue. Please confirm before I plan the change.",
            "",
            f"Title: {req.get('title', '')}",
            f"Problem: {req.get('problem', '')}",
            "Goals:",
            *[f"  - {g}" for g in req.get("goals", [])],
            "Acceptance criteria:",
            *[f"  - {a}" for a in req.get("acceptance_criteria", [])],
            "In scope:",
            *[f"  - {s}" for s in req.get("in_scope", [])],
            "Out of scope:",
            *[f"  - {s}" for s in req.get("out_of_scope", [])],
        ]
        if req.get("open_questions"):
            lines += ["Open questions:", *[f"  - {q}" for q in req["open_questions"]]]
    elif kind == "plan_approval":
        plan = interrupt_value.get("plan") or {}
        lines = ["Here is my implementation plan, grounded in the repository.", ""]
        if plan.get("notes_response"):
            lines += ["Reply to your notes:", plan["notes_response"], ""]
        lines += ["Analysis:", plan.get("analysis", ""), "", "Planned edits:"]
        for i, s in enumerate(plan.get("plan_steps", []), 1):
            where = f"{s['repo']}/{s['file']}" if s.get("repo") else s.get("file")
            lines.append(f"  {i}. [{s.get('action')}] {where}: {s.get('change')}")
        if plan.get("phases"):
            lines.append("Phases:")
            for p in plan["phases"]:
                idx = ", ".join(str(i + 1) for i in p.get("step_indexes", []))
                lines.append(f"  - {p.get('name')}: steps {idx}")
        lines += ["Verification:", *[f"  - {v}" for v in plan.get("verification", [])]]
        if plan.get("open_questions"):
            lines += ["Open questions:", *[f"  - {q}" for q in plan["open_questions"]]]
    elif kind == "phase_gate":
        lines = [
            f"Phase {interrupt_value.get('phase_index', 0) + 1} of {interrupt_value.get('phases_total', 1)} "
            "is implemented and passed review.",
            f"Files changed so far: {', '.join(interrupt_value.get('files_changed', [])) or '-'}",
            *_partial_review_note(interrupt_value),
        ]
    elif kind == "final_review":
        lines = [
            "All planned changes are implemented and passed review.",
            f"Files changed: {', '.join(interrupt_value.get('files_changed', [])) or '-'}",
            f"Diff size: {interrupt_value.get('diff_lines', 0)} lines (open the UI to inspect it).",
            *_partial_review_note(interrupt_value),
        ]
        if not interrupt_value.get("pr_enabled", False):
            lines.append("Pull request creation is disabled in this environment; only /finish is available.")
    else:
        lines = [f"Waiting for input ({kind})."]
    lines += ["", command_help(cast(str, kind))]
    return "\n".join(lines)


def approver_account_ids(rules: list[str], issue: JiraIssue | None) -> set[str]:
    """The Atlassian account ids allowed to answer this issue's gates.

    Fails closed: a rule that cannot be resolved (an issue started inline has no reporter
    id) adds nobody, and no rules means nobody.
    """
    allowed: set[str] = set()
    for rule in rules:
        if rule == "reporter" and issue:
            allowed.add(issue.reporter_account_id)
        elif rule == "assignee" and issue:
            allowed.add(issue.assignee_account_id)
        elif rule.startswith("account:"):
            allowed.add(rule[8:].strip())
    allowed.discard("")
    return allowed


class JiraCommentChannel:
    def __init__(self, jira: JiraClient, *, agent_account_id: str = ""):
        self.jira = jira
        self.agent_account_id = agent_account_id

    async def notify_pending(self, issue_key: str, interrupt_value: dict) -> None:
        try:
            await self.jira.add_comment(issue_key, render_pending(interrupt_value))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post Jira feedback request for %s", issue_key)

    async def notify_text(self, issue_key: str, text: str) -> None:
        try:
            await self.jira.add_comment(issue_key, text)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post Jira comment for %s", issue_key)

    def decision_from_comment(self, pending_type: str, body: str, author_account_id: str = "") -> dict | None:
        if self.agent_account_id and author_account_id == self.agent_account_id:
            return None  # ignore our own comments
        return parse_comment_command(pending_type, body)
