import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ... import prompts, untrusted, usage
from ...config import StageModelConfig
from ...models import AgentResult, JiraIssue, RequirementsSpec
from .. import progress
from ..state import AsyncNode, GraphState
from .coding_agent import phase_plan

logger = logging.getLogger(__name__)

ReviewNode = AsyncNode

# Dropped first when a diff is over the limit: nobody reviews these line by line.
_LOW_VALUE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
}
_LOW_VALUE_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".snap")
_FILE_HEADER = re.compile(r"(?m)^(?=diff --git )")
_HEADER_PATH = re.compile(r"^diff --git a/.* b/(.*)$", re.MULTILINE)


def cap_diffs(diffs: dict[str, str], max_chars: int) -> tuple[dict[str, str], list[str]]:
    """Trim ``diffs`` to ``max_chars`` by leaving out whole files; returns (diffs, omitted files).

    A hunk cut in half reads like a broken change, so files go whole: lockfiles and
    generated files first, then the largest remaining ones. ``max_chars <= 0`` disables the cap.
    """
    if max_chars <= 0 or sum(len(text) for text in diffs.values()) <= max_chars:
        return diffs, []

    qualify = len(diffs) > 1
    chunks: list[dict] = []
    for repo, text in diffs.items():
        for chunk in _FILE_HEADER.split(text):
            if not chunk:
                continue
            match = _HEADER_PATH.search(chunk)
            path = match.group(1).strip() if match else "(unknown file)"
            name = path.rsplit("/", 1)[-1]
            chunks.append(
                {
                    "repo": repo,
                    "label": f"{repo}/{path}" if qualify else path,
                    "text": chunk,
                    "low_value": name in _LOW_VALUE_NAMES or name.endswith(_LOW_VALUE_SUFFIXES),
                    "keep": True,
                }
            )

    total = sum(len(c["text"]) for c in chunks)
    for chunk in sorted(chunks, key=lambda c: (not c["low_value"], -len(c["text"]))):
        if total <= max_chars:
            break
        chunk["keep"] = False
        total -= len(chunk["text"])

    kept: dict[str, str] = {}
    for chunk in chunks:
        if chunk["keep"]:
            kept[chunk["repo"]] = kept.get(chunk["repo"], "") + chunk["text"]
    return kept, [c["label"] for c in chunks if not c["keep"]]


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
    omitted_files: list[str] | None = None,
    earlier_phases: int = 0,
) -> str:
    # The summary is Jira text; the approved requirement below it is what a human signed off.
    parts = [untrusted.wrap([f"Jira issue: {issue.key} - {untrusted.neutralise(issue.summary)}"])]
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
        parts.append(untrusted.wrap([f"Description: {untrusted.neutralise(issue.description)}"]))
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
    if earlier_phases:
        parts += [
            "",
            f"This is phase {earlier_phases + 1}. The diff holds only this phase's changes; the "
            f"{earlier_phases} earlier phase(s) were reviewed separately and are already in the code.",
        ]
    if omitted_files:
        parts += [
            "",
            "The diff is over the size limit, so these changed files were left out and you cannot see them:",
            *[f"  - {name}" for name in omitted_files],
            "Review what is shown. Do not reject because the content of a left-out file is missing; "
            "say in your comments that those files were not reviewed.",
        ]
    parts.append("\n" + _render_diffs(diffs))
    return "\n".join(parts)


def make_review_agent(
    llm,
    *,
    review_rules: str = "",
    max_diff_chars: int = 0,
    model: StageModelConfig | None = None,
    prices: usage.Prices | None = None,
    budget_usd: float = 0.0,
) -> ReviewNode:
    structured_llm = llm.with_structured_output(ReviewVerdict)
    cfg = model or StageModelConfig(provider="", model="", api_key="")

    async def review_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        progress.mark(issue.key, "review_agent")
        usage.check_budget(state, budget_usd)
        # Only this phase's changes: earlier phases passed their own review, and judging
        # them again against this phase's plan steps produced spurious rejections.
        phase_diff = state.get("phase_diff")
        diffs, omitted_files = cap_diffs(
            phase_diff if phase_diff is not None else state.get("diffs") or {}, max_diff_chars
        )
        iteration = state.get("iteration", 0)
        previous_feedback = state.get("review_feedback", "")
        plan = state.get("plan_result")
        current = phase_plan(plan, state.get("execution_mode", "all"), state.get("phase_index", 0)) if plan else None

        logger.info("Reviewing issue %s (iteration=%d)", issue.key, iteration)
        diff_lines = sum(text.count("\n") for text in diffs.values())
        scope = f" across {len(diffs)} repos" if len(diffs) > 1 else ""
        progress.add_event(
            issue.key,
            {
                "source": "llm",
                "ev": "llm_call",
                "stage": "review",
                "text": f"reviewing {diff_lines}-line diff{scope} (attempt {iteration})"
                + (f", {len(omitted_files)} file(s) left out as too large" if omitted_files else ""),
            },
        )
        verdict, used = await usage.tracked(
            structured_llm,
            [
                SystemMessage(content=prompts.get("phase_review").text),
                HumanMessage(
                    content=_build_review_prompt(
                        issue,
                        diffs,
                        requirements=state.get("requirements"),
                        plan=current,
                        rules=review_rules,
                        iteration=iteration,
                        previous_feedback=previous_feedback,
                        omitted_files=omitted_files,
                        earlier_phases=(state.get("phase_index", 0) if state.get("execution_mode") == "phased" else 0),
                    )
                ),
            ],
            stage="review",
            cfg=cfg,
            prices=prices or {},
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
                "text": ("approved" if verdict.approved and not verdict.must_violations else "rejected")
                + ": "
                + verdict.comments[:200],
            },
        )

        return {
            "review_approved": verdict.approved and not verdict.must_violations,
            "review_feedback": feedback,
            "review_omitted_files": omitted_files,
            "usage": usage.appended(state, used),
            "current_node": "review_agent",
        }

    return review_agent
