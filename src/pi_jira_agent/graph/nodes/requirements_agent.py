import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...models import RequirementsSpec, ScopeCheck
from .. import progress
from ..state import GraphState

logger = logging.getLogger(__name__)

_FRAMING_SYSTEM = (
    "You are a senior business analyst working with an engineering team. You receive a Jira "
    "issue (summary, description and the full comment thread) and must frame it as a precise, "
    "testable requirement that an engineer can plan against.\n"
    "Rules:\n"
    "- Use only what the issue and its comments actually say; do not invent features.\n"
    "- Comments often refine or override the description: later comments win, and the reporter's "
    "or assignee's statements outrank speculation by others.\n"
    "- Acceptance criteria must be observable and testable, one behaviour each.\n"
    "- Put anything the issue mentions but does not ask to change under out_of_scope.\n"
    "- Put anything only the reporter can settle under open_questions; do not guess.\n"
    "- In sources, say where each goal/criterion came from (e.g. 'description', "
    "'comment by <name>', 'inferred from ...')."
)

_SCOPE_SYSTEM = (
    "You are a scope reviewer. You compare a requirement that an analyst framed from a Jira "
    "issue with the version a human edited afterwards. For every material addition, removal or "
    "change the human made, decide whether it stays inside the scope of the original Jira issue "
    "(description plus comments) or extends it.\n"
    "- 'added' items that the issue never asked for are out_of_scope.\n"
    "- Clarifications, rewording, and tightening of existing goals are in_scope.\n"
    "- Removing an original goal is 'removed' and in_scope (narrowing is allowed) but say so.\n"
    "- If you cannot tell, use 'unclear' and explain.\n"
    "Return an empty findings list when the edit is purely cosmetic."
)


def make_requirements_agent(llm):
    structured = llm.with_structured_output(RequirementsSpec)

    async def requirements_agent(state: GraphState) -> dict:
        issue = state["issue"]
        progress.mark(issue.key, "requirements_agent")
        notes = state.get("requirements_notes", "")
        previous = state.get("requirements")

        parts = [issue.as_context()]
        if notes and previous is not None:
            parts += [
                "",
                "Your previous framing:",
                json.dumps(previous.model_dump(), indent=2),
                "",
                "The human reviewer asked you to revise it with these notes:",
                notes,
                "",
                "Return the complete revised requirement.",
            ]
        logger.info("Framing requirements for %s%s", issue.key, " (revision)" if notes else "")
        progress.add_event(
            issue.key,
            {
                "source": "llm",
                "ev": "llm_call",
                "stage": "requirements",
                "text": "revising with reviewer notes" if notes else "framing from issue + comments",
            },
        )
        spec: RequirementsSpec = await structured.ainvoke(
            [SystemMessage(content=_FRAMING_SYSTEM), HumanMessage(content="\n".join(parts))]
        )
        progress.add_event(
            issue.key,
            {
                "source": "llm",
                "ev": "llm_done",
                "stage": "requirements",
                "text": f"{len(spec.goals)} goal(s), {len(spec.acceptance_criteria)} acceptance criteria",
            },
        )
        return {
            "requirements": spec,
            "requirements_original": spec,
            "requirements_notes": "",
            "scope_check": None,
            "scope_acknowledged": False,
            "status": "pending_requirements",
            "current_node": "requirements_agent",
        }

    return requirements_agent


def make_scope_check(llm):
    structured = llm.with_structured_output(ScopeCheck)

    async def scope_check(state: GraphState) -> dict:
        issue = state["issue"]
        progress.mark(issue.key, "scope_check")
        original: RequirementsSpec = state["requirements_original"]
        edited: RequirementsSpec = state["requirements"]

        if state.get("scope_acknowledged") or original.model_dump() == edited.model_dump():
            return {"scope_check": ScopeCheck(), "status": "planning", "current_node": "scope_check"}

        prompt = "\n".join(
            [
                "Original Jira issue:",
                issue.as_context(),
                "",
                "Analyst's framing (baseline):",
                json.dumps(original.model_dump(), indent=2),
                "",
                "Human-edited version:",
                json.dumps(edited.model_dump(), indent=2),
            ]
        )
        logger.info("Checking scope of edited requirements for %s", issue.key)
        progress.add_event(
            issue.key,
            {"source": "llm", "ev": "llm_call", "stage": "scope_check", "text": "comparing your edits with the issue"},
        )
        result: ScopeCheck = await structured.ainvoke(
            [SystemMessage(content=_SCOPE_SYSTEM), HumanMessage(content=prompt)]
        )
        progress.add_event(
            issue.key,
            {
                "source": "llm",
                "ev": "llm_done",
                "stage": "scope_check",
                "text": f"{len(result.out_of_scope_items)} out-of-scope item(s)",
            },
        )
        if result.out_of_scope_items:
            logger.info("Scope check for %s flagged %d item(s)", issue.key, len(result.out_of_scope_items))
            return {"scope_check": result, "status": "pending_requirements", "current_node": "scope_check"}
        return {"scope_check": result, "status": "planning", "current_node": "scope_check"}

    return scope_check
