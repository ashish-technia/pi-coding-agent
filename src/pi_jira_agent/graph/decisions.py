"""Human decisions, independent of the channel that delivers them.

Every pause in the graph is a LangGraph interrupt whose value carries a ``type``.
The UI and the Jira-comment channel both turn user input into one of the decision
models below; the graph only ever sees validated dicts.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..models import RequirementsSpec

InterruptType = Literal["requirements_approval", "plan_approval", "phase_gate", "final_review"]


# --- requirements_approval --------------------------------------------------


class RequirementsApprove(BaseModel):
    type: Literal["requirements_approval"] = "requirements_approval"
    action: Literal["approve"] = "approve"
    requirements: RequirementsSpec | None = Field(
        default=None, description="Edited requirements; None keeps the agent's version."
    )
    acknowledge_scope: bool = Field(
        default=False,
        description="True when the human explicitly keeps items flagged as out of scope.",
    )


class RequirementsRevise(BaseModel):
    type: Literal["requirements_approval"] = "requirements_approval"
    action: Literal["revise"] = "revise"
    notes: str = Field(min_length=1)


class RequirementsCancel(BaseModel):
    type: Literal["requirements_approval"] = "requirements_approval"
    action: Literal["cancel"] = "cancel"


# --- plan_approval ------------------------------------------------------------


class PlanApprove(BaseModel):
    type: Literal["plan_approval"] = "plan_approval"
    action: Literal["approve"] = "approve"
    mode: Literal["all", "phased"] = "all"


class PlanRefine(BaseModel):
    type: Literal["plan_approval"] = "plan_approval"
    action: Literal["refine"] = "refine"
    notes: str = Field(min_length=1)


class PlanReject(BaseModel):
    type: Literal["plan_approval"] = "plan_approval"
    action: Literal["reject"] = "reject"


# --- phase_gate ----------------------------------------------------------------


class PhaseContinue(BaseModel):
    type: Literal["phase_gate"] = "phase_gate"
    action: Literal["continue"] = "continue"


class PhaseStop(BaseModel):
    type: Literal["phase_gate"] = "phase_gate"
    action: Literal["stop"] = "stop"


# --- final_review --------------------------------------------------------------


class FinalCreatePr(BaseModel):
    type: Literal["final_review"] = "final_review"
    action: Literal["create_pr"] = "create_pr"
    pr_title: str = Field(min_length=1)
    pr_description: str | None = None


class FinalFinish(BaseModel):
    type: Literal["final_review"] = "final_review"
    action: Literal["finish"] = "finish"


# Pydantic's smart union picks the member whose (type, action) literals match.
Decision = (
    RequirementsApprove
    | RequirementsRevise
    | RequirementsCancel
    | PlanApprove
    | PlanRefine
    | PlanReject
    | PhaseContinue
    | PhaseStop
    | FinalCreatePr
    | FinalFinish
)

_adapter = TypeAdapter(Decision)

ALLOWED_ACTIONS: dict[str, set[str]] = {
    "requirements_approval": {"approve", "revise", "cancel"},
    "plan_approval": {"approve", "refine", "reject"},
    "phase_gate": {"continue", "stop"},
    "final_review": {"create_pr", "finish"},
}


def parse_decision(pending_type: str, payload: dict) -> BaseModel:
    """Validate a raw decision against the interrupt currently pending."""
    if pending_type not in ALLOWED_ACTIONS:
        raise ValueError(f"Unknown pending interrupt type {pending_type!r}.")
    action = payload.get("action")
    if action not in ALLOWED_ACTIONS[pending_type]:
        raise ValueError(
            f"Action {action!r} is not valid while waiting for {pending_type}; "
            f"expected one of {sorted(ALLOWED_ACTIONS[pending_type])}."
        )
    try:
        return _adapter.validate_python({**payload, "type": pending_type})
    except ValidationError as exc:
        raise ValueError(exc.errors()[0].get("msg", str(exc))) from exc


# --- Jira comment commands (flow 2) -----------------------------------------
#
# Humans reply to the agent's Jira comment with one of these on the first line:
#   /approve                      (requirements, plan, phase or final stage)
#   /approve phased               (plan only: implement phase by phase)
#   /revise <notes>               (requirements or plan)
#   /reject                       (plan)  |  /cancel (requirements)
#   /continue | /stop             (phase gate)
#   /pr <title>                   (final review: create the pull request)
#   /finish                       (final review: end without a PR)

_COMMAND_RE = re.compile(r"^\s*/(?P<cmd>[a-z]+)\b\s*(?P<arg>.*)$", re.IGNORECASE | re.DOTALL)


def command_help(pending_type: str) -> str:
    """The reply schema the agent includes in every Jira comment."""
    lines = {
        "requirements_approval": [
            "/approve - accept these requirements and start planning",
            "/revise <notes> - ask me to re-frame the requirements",
            "/cancel - stop this run",
        ],
        "plan_approval": [
            "/approve - implement the whole plan",
            "/approve phased - implement one phase at a time, pausing after each",
            "/revise <notes> - ask me to refine the plan",
            "/reject - stop this run",
        ],
        "phase_gate": [
            "/continue - implement the next phase",
            "/stop - stop after this phase and go to final review",
        ],
        "final_review": [
            "/pr <pull request title> - commit, push and open the pull request",
            "/finish - end the run without a pull request",
        ],
    }[pending_type]
    return "Reply with one of:\n" + "\n".join(lines)


def parse_comment_command(pending_type: str, comment_body: str) -> dict | None:
    """Turn a human's Jira reply into a decision payload, or None if it is not a command."""
    match = _COMMAND_RE.match(comment_body or "")
    if not match:
        return None
    cmd = match.group("cmd").lower()
    arg = (match.group("arg") or "").strip()

    if pending_type == "requirements_approval":
        if cmd == "approve":
            return {"action": "approve"}
        if cmd == "revise" and arg:
            return {"action": "revise", "notes": arg}
        if cmd in {"cancel", "reject"}:
            return {"action": "cancel"}
    elif pending_type == "plan_approval":
        if cmd == "approve":
            return {"action": "approve", "mode": "phased" if arg.lower().startswith("phase") else "all"}
        if cmd in {"revise", "refine"} and arg:
            return {"action": "refine", "notes": arg}
        if cmd in {"reject", "cancel"}:
            return {"action": "reject"}
    elif pending_type == "phase_gate":
        if cmd in {"continue", "approve", "next"}:
            return {"action": "continue"}
        if cmd == "stop":
            return {"action": "stop"}
    elif pending_type == "final_review":
        if cmd == "pr" and arg:
            return {"action": "create_pr", "pr_title": arg}
        if cmd in {"finish", "done", "approve"}:
            return {"action": "finish"}
    return None
