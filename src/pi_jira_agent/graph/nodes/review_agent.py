import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ...models import AgentResult, JiraIssue, RequirementsSpec
from .. import progress
from ..state import AsyncNode, GraphState
from .coding_agent import phase_plan

logger = logging.getLogger(__name__)

ReviewNode = AsyncNode

_SYSTEM_PROMPT = (
    "You are a senior engineer performing code review. You are given the approved requirement, "
    "the approved implementation plan for the current phase, the team's review rules, and the "
    "git diff an automated coding agent produced. Decide whether the diff correctly and safely "
    "implements the plan and satisfies the acceptance criteria that this phase covers.\n"
    "- Every 'Must' rule violated is a rejection. 'Should' rules are observations unless several "
    "are broken.\n"
    "- Flag bugs, missing plan steps, unrequested changes, security issues and risky edits.\n"
    "- Be concise and specific: reference file names and the code in question.\n"
    "- If the diff is empty, reject and say that no changes were made.\n"
    "- If this is a retry, verify the previous feedback was actually addressed.\n"
    "- A change may span several repositories, shown as one diff per repository. Judge them "
    "together: a call added in one repo must match the signature defined in another, and a "
    "symbol that looks undefined may simply live in a sibling repo's diff."
)


def _render_diffs(diffs: dict[str, str]) -> str:
    """One fenced diff per repository, so the reviewer can tell them apart."""
    present = {name: text for name, text in (diffs or {}).items() if text.strip()}
    if not present:
        return "Diff:\n```diff\n(no changes produced)\n```"
    if len(present) == 1:
        (only,) = present.values()
        return f"Diff:\n```diff\n{only}\n```"
    blocks = [f"Diff in repo '{name}':\n```diff\n{text}\n```" for name, text in present.items()]
    return "\n\n".join(blocks)


class ReviewVerdict(BaseModel):
    approved: bool = Field(description="True if the diff is acceptable to merge as-is.")
    comments: str = Field(
        description="Specific feedback explaining the decision. If approved, note any minor observations. "
        "If rejected, list exactly what must be fixed."
    )
    must_violations: list[str] = Field(default_factory=list, description="Review 'Must' rules that were broken.")


def _build_review_prompt(
    issue: JiraIssue,
    diffs: dict[str, str],
    *,
    requirements: RequirementsSpec | None,
    plan: AgentResult | None,
    rules: str,
    iteration: int,
    previous_feedback: str,
) -> str:
    parts = [f"Jira issue: {issue.key} - {issue.summary}"]
    if requirements:
        parts += [
            "",
            "Approved requirement:",
            f"  Problem: {requirements.problem}",
            "  Goals:",
            *[f"    - {g}" for g in requirements.goals],
            "  Acceptance criteria:",
            *[f"    - {a}" for a in requirements.acceptance_criteria],
            "  Out of scope:",
            *[f"    - {o}" for o in requirements.out_of_scope],
        ]
    else:
        parts.append(f"Description: {issue.description}")
    if plan:
        parts += ["", "Approved plan for this phase:", f"  Branch: {plan.branch_name}"]
        if plan.analysis:
            parts.append(f"  Analysis: {plan.analysis}")
        parts.append("  Planned edits (the diff must implement each of these):")
        for step in plan.plan_steps:
            where = f"{step.repo}/{step.file}" if step.repo else step.file
            parts.append(f"    - [{step.action}] {where}: {step.change}")
        if plan.verification:
            parts.append("  Verification the plan called for:")
            parts += [f"    - {v}" for v in plan.verification]
    if rules.strip():
        parts += ["", "Team review rules:", rules.strip()]
    if iteration > 1 and previous_feedback:
        parts += [
            "",
            f"This is retry attempt {iteration}. Previous review feedback that must be addressed:",
            f"  {previous_feedback}",
        ]
    parts.append("\n" + _render_diffs(diffs))
    return "\n".join(parts)


def make_review_agent(llm, *, review_rules: str = "") -> ReviewNode:
    structured_llm = llm.with_structured_output(ReviewVerdict)

    async def review_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        progress.mark(issue.key, "review_agent")
        diffs = state.get("diffs") or {}
        iteration = state.get("iteration", 0)
        previous_feedback = state.get("review_feedback", "")
        plan = state.get("plan_result")
        current = (
            phase_plan(plan, state.get("execution_mode", "all"), state.get("phase_index", 0)) if plan else None
        )

        logger.info("Reviewing issue %s (iteration=%d)", issue.key, iteration)
        diff_lines = sum(text.count("\n") for text in diffs.values())
        scope = f" across {len(diffs)} repos" if len(diffs) > 1 else ""
        progress.add_event(
            issue.key,
            {"source": "llm", "ev": "llm_call", "stage": "review", "text": f"reviewing {diff_lines}-line diff{scope} (attempt {iteration})"},
        )
        verdict: ReviewVerdict = await structured_llm.ainvoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=_build_review_prompt(
                        issue,
                        diffs,
                        requirements=state.get("requirements"),
                        plan=current,
                        rules=review_rules,
                        iteration=iteration,
                        previous_feedback=previous_feedback,
                    )
                ),
            ]
        )
        logger.info(
            "Review verdict for %s: approved=%s must_violations=%d",
            issue.key,
            verdict.approved,
            len(verdict.must_violations),
        )
        feedback = verdict.comments
        if verdict.must_violations:
            feedback += "\nRule violations: " + "; ".join(verdict.must_violations)
        progress.add_event(
            issue.key,
            {
                "source": "llm",
                "ev": "llm_done",
                "stage": "review",
                "text": ("approved" if verdict.approved and not verdict.must_violations else "rejected") + ": " + verdict.comments[:200],
            },
        )

        return {
            "review_approved": verdict.approved and not verdict.must_violations,
            "review_feedback": feedback,
            "current_node": "review_agent",
        }

    return review_agent
