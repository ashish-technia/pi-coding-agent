"""Human-in-the-loop pause points.

Each gate calls ``interrupt`` with a typed payload. The service validates the
human's reply (see decisions.py) before resuming, so the dicts here are trusted.
On resume LangGraph re-executes the node from the top, so gates must be pure
until the interrupt call.
"""

import logging

from langgraph.types import interrupt

from ...models import RequirementsSpec
from .. import progress
from ..state import GraphState

logger = logging.getLogger(__name__)


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
    action = decision.get("action")
    if action == "approve":
        edited = decision.get("requirements")
        requirements = RequirementsSpec.model_validate(edited) if edited else state["requirements"]
        return {
            "requirements": requirements,
            "scope_acknowledged": bool(decision.get("acknowledge_scope", False)),
            "status": "planning",
            "current_node": "await_requirements",
        }
    if action == "revise":
        return {
            "requirements_notes": decision.get("notes", ""),
            "status": "framing_requirements",
            "current_node": "await_requirements",
        }
    return {"status": "cancelled", "current_node": "await_requirements"}


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
            "status": "coding",
            "current_node": "await_plan",
        }
    if action == "refine":
        return {"plan_notes": decision.get("notes", ""), "status": "planning", "current_node": "await_plan"}
    return {"status": "cancelled", "current_node": "await_plan"}


def phase_gate(state: GraphState) -> dict:
    """After a phase passes review: pause between phases in phased mode, else go to final."""
    key = state["issue"].key
    progress.mark(key, "phase_gate")
    mode = state.get("execution_mode", "all")
    index = state.get("phase_index", 0)
    total = state.get("phases_total", 1)
    diffs = state.get("diffs") or {}
    phase_diffs = list(state.get("phase_diffs") or []) + [diffs]
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
            "review_feedback": state.get("review_feedback", ""),
            "review_omitted_files": state.get("review_omitted_files") or [],
        }
    )
    if decision.get("action") == "continue":
        return {
            "phase_diffs": phase_diffs,
            "phase_index": index + 1,
            "iteration": 0,
            "review_feedback": "",
            "status": "coding",
            "current_node": "phase_gate",
        }
    return {"phase_diffs": phase_diffs, "status": "pending_final", "current_node": "phase_gate"}


def make_await_final(*, pr_enabled: bool):
    def await_final(state: GraphState) -> dict:
        key = state["issue"].key
        progress.mark(key, "await_final")
        code_result = state.get("code_result")
        plan = state.get("plan_result")
        diffs = state.get("diffs") or {}
        suggestion = code_result or plan
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
            }
        )
        if decision.get("action") == "create_pr" and pr_enabled:
            updates: dict = {
                "pr_title": decision.get("pr_title", "").strip(),
                "status": "creating_pr",
                "current_node": "await_final",
            }
            description = decision.get("pr_description")
            if description and code_result:
                updates["code_result"] = code_result.model_copy(update={"pr_description": description})
            return updates
        return {"status": "done", "current_node": "await_final"}

    return await_final
