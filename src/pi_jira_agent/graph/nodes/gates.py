"""Human-in-the-loop pause points.

Each gate calls ``interrupt`` with a typed payload. The service validates the
human's reply (see decisions.py) before resuming, so the dicts here are trusted.
On resume LangGraph re-executes the node from the top, so gates must be pure
until the interrupt call.
"""

import datetime as dt
import logging

from langgraph.types import interrupt

from ...models import RequirementsSpec, ReviewResult
from .. import progress
from ..state import GraphState
from .pr_review import render_findings

logger = logging.getLogger(__name__)


def _logged(state: GraphState, gate: str, decision: dict, **extra) -> dict:
    """The state update that appends this decision to the run's log: which gate, what, who, when.

    `decided_by` is added by the service ("ui", or "jira:<accountId>"), never by the caller.
    `extra` is recorded with the entry (the final gate adds what the PR review had found).
    """
    entry = {
        "gate": gate,
        "action": decision.get("action"),
        "by": decision.get("decided_by") or "unknown",
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        **extra,
    }
    return {"decision_log": [*(state.get("decision_log") or []), entry]}


def await_requirements(state: GraphState) -> dict:
    key = state["issue"].key
    progress.mark(key, "await_requirements")
    scope = state.get("scope_check")
    decision = interrupt(
        {
            "type": "requirements_approval",
            "issue_key": key,
            "requirements": state["requirements"].model_dump(),
            "scope_check": scope.model_dump() if scope else None,
        }
    )
    log = _logged(state, "requirements_approval", decision)
    action = decision.get("action")
    if action == "approve":
        edited = decision.get("requirements")
        requirements = RequirementsSpec.model_validate(edited) if edited else state["requirements"]
        return {
            "requirements": requirements,
            "scope_acknowledged": bool(decision.get("acknowledge_scope", False)),
            "status": "planning",
            "current_node": "await_requirements",
            **log,
        }
    if action == "revise":
        return {
            "requirements_notes": decision.get("notes", ""),
            "status": "framing_requirements",
            "current_node": "await_requirements",
            **log,
        }
    return {"status": "cancelled", "current_node": "await_requirements", **log}


def await_plan(state: GraphState) -> dict:
    key = state["issue"].key
    progress.mark(key, "await_plan")
    decision = interrupt(
        {
            "type": "plan_approval",
            "issue_key": key,
            "plan": state["plan_result"].model_dump(),
            "phases_total": state.get("phases_total", 1),
        }
    )
    log = _logged(state, "plan_approval", decision)
    action = decision.get("action")
    if action == "approve":
        mode = decision.get("mode", "all")
        if mode == "phased" and state.get("phases_total", 1) <= 1:
            mode = "all"
        return {
            "execution_mode": mode,
            "phase_index": 0,
            "iteration": 0,
            "review_feedback": "",
            "phase_diffs": [],
            "phase_base": {},
            "status": "coding",
            "current_node": "await_plan",
            **log,
        }
    if action == "refine":
        return {"plan_notes": decision.get("notes", ""), "status": "planning", "current_node": "await_plan", **log}
    return {"status": "cancelled", "current_node": "await_plan", **log}


def phase_gate(state: GraphState) -> dict:
    """After a phase passes review: pause between phases in phased mode, else go to final."""
    key = state["issue"].key
    progress.mark(key, "phase_gate")
    mode = state.get("execution_mode", "all")
    index = state.get("phase_index", 0)
    total = state.get("phases_total", 1)
    diffs = state.get("diffs") or {}
    phase_diff = state.get("phase_diff")
    if phase_diff is None:  # a checkpoint written before phases had their own diff
        phase_diff = diffs
    phase_diffs = list(state.get("phase_diffs") or []) + [phase_diff]
    code_result = state.get("code_result")

    if mode != "phased" or index + 1 >= total:
        return {"phase_diffs": phase_diffs, "status": "pending_final", "current_node": "phase_gate"}

    decision = interrupt(
        {
            "type": "phase_gate",
            "issue_key": key,
            "phase_index": index,
            "phases_total": total,
            "files_changed": code_result.files_changed if code_result else [],
            "diffs": diffs,
            "phase_diff": phase_diff,
            "review_feedback": state.get("review_feedback", ""),
            "review_omitted_files": state.get("review_omitted_files") or [],
        }
    )
    log = _logged(state, "phase_gate", decision)
    if decision.get("action") == "continue":
        return {
            "phase_diffs": phase_diffs,
            # The next phase is diffed against the tree this one ended on.
            "phase_base": state.get("tree_shas") or {},
            "phase_index": index + 1,
            "iteration": 0,
            "review_feedback": "",
            "status": "coding",
            "current_node": "phase_gate",
            **log,
        }
    return {"phase_diffs": phase_diffs, "status": "pending_final", "current_node": "phase_gate", **log}


KNOWN_FINDINGS_HEADING = "## Known review findings"


def known_findings_section(review: ReviewResult | None) -> str:
    """What the PR description says about `must` findings the human accepted (empty if none)."""
    left = [f for f in (review.findings if review else []) if f.severity == "must"]
    if not left:
        return ""
    lines = [
        KNOWN_FINDINGS_HEADING,
        "",
        "An automated review of the whole change reported these and they were accepted as they are:",
        "",
    ]
    for f in left:
        where = f"{f.repo}/{f.file}" if f.repo else f.file
        location = f"`{where}:{f.line}` " if where and f.line else f"`{where}` " if where else ""
        lines.append(f"- {location}{f.claim}")
    return "\n".join(lines)


def pr_review_block(state: GraphState, *, max_fix_rounds: int) -> dict:
    """The PR review as the final gate shows it. Pure: read from state only."""
    review: ReviewResult | None = state.get("pr_review")
    enabled = bool(state.get("pr_review_enabled"))
    rounds = state.get("fix_rounds") or 0
    return {
        "enabled": enabled,
        "skipped": bool(state.get("pr_review_skipped")),
        "summary": review.summary if review else "",
        "findings": [f.model_dump() for f in review.findings] if review else [],
        "resolved": list(review.resolved) if review else [],
        "not_reviewed": list(review.not_reviewed) if review else [],
        "dropped_findings": len(review.dropped_findings) if review else 0,
        "fix_rounds": rounds,
        "max_fix_rounds": max_fix_rounds,
        # Offered only while there is a review to act on and rounds are left.
        "fix_available": enabled and review is not None and rounds < max_fix_rounds,
    }


def make_await_final(*, pr_enabled: bool, max_fix_rounds: int = 0):
    def await_final(state: GraphState) -> dict:
        key = state["issue"].key
        progress.mark(key, "await_final")
        code_result = state.get("code_result")
        plan = state.get("plan_result")
        diffs = state.get("diffs") or {}
        suggestion = code_result or plan
        review: ReviewResult | None = state.get("pr_review")
        review_block = pr_review_block(state, max_fix_rounds=max_fix_rounds)
        decision = interrupt(
            {
                "type": "final_review",
                "issue_key": key,
                "diffs": diffs,
                "diff_lines": sum(text.count("\n") for text in diffs.values()),
                "files_changed": code_result.files_changed if code_result else [],
                "review_feedback": state.get("review_feedback", ""),
                "review_omitted_files": state.get("review_omitted_files") or [],
                "pr_enabled": pr_enabled,
                "suggested_pr_title": suggestion.pr_title if suggestion else "",
                "suggested_pr_description": suggestion.pr_description if suggestion else "",
                "phases_completed": (
                    len(state.get("phase_diffs") or [])
                    if state.get("execution_mode") == "phased"
                    else state.get("phases_total", 1)
                ),
                "phases_total": state.get("phases_total", 1),
                "pr_review": review_block,
            }
        )
        # What the reviewer showed and what the human did with it, kept for measuring the
        # reviewer against human verdicts later (R-20).
        shown = review.findings if review else []
        chosen = set(decision.get("findings") or []) if decision.get("action") == "fix" else set()
        log = _logged(
            state,
            "final_review",
            decision,
            **(
                {
                    "review": {
                        "shown": [{"number": f.number, "severity": f.severity, "category": f.category} for f in shown],
                        "fix": sorted(n for n in chosen if any(f.number == n for f in shown)),
                        "left": [f.number for f in shown if f.number not in chosen],
                    }
                }
                if review_block["enabled"]
                else {}
            ),
        )
        if decision.get("action") == "fix" and review_block["fix_available"] and review:
            findings = [f for f in review.findings if f.number in chosen]
            notes = (decision.get("notes") or "").strip()
            return {
                # `feedback` is the fix pass's work order. It is kept apart from
                # `review_feedback`, which stays the phase reviewer's last word.
                "fix_request": {
                    "findings": [f.model_dump() for f in findings],
                    "notes": notes,
                    "feedback": render_findings(findings, notes),
                },
                "fix_rounds": review_block["fix_rounds"] + 1,
                "status": "coding",
                "current_node": "await_final",
                **log,
            }
        if decision.get("action") == "create_pr" and pr_enabled:
            updates: dict = {
                "pr_title": decision.get("pr_title", "").strip(),
                "status": "creating_pr",
                "current_node": "await_final",
                **log,
            }
            description = decision.get("pr_description") or (code_result.pr_description if code_result else "")
            known = known_findings_section(review)
            if known and KNOWN_FINDINGS_HEADING not in description:
                description = f"{description}\n\n{known}".strip()
            if code_result and description != code_result.pr_description:
                updates["code_result"] = code_result.model_copy(update={"pr_description": description})
            return updates
        return {"status": "done", "current_node": "await_final", **log}

    return await_final
