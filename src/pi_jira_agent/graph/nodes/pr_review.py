"""The whole-change review before the final gate (R-56).

`review_agent` judges one phase's diff against that phase's plan steps. This node runs once
the last phase is accepted and judges everything the run changed, in every repository,
against the approved requirement. It is advisory: its findings are shown at the final gate
and a human decides what to do with them.
"""

import logging

from ... import usage
from ...config import StageModelConfig
from ...models import ReviewFinding, ReviewResult
from ...pi_agent import PiAgentExecutor
from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, selected_repos
from ..slots import PiSlots
from ..state import AsyncNode, GraphState

logger = logging.getLogger(__name__)


def render_findings(findings: list[ReviewFinding], notes: str = "") -> str:
    """Findings as the work order of a fix pass: what the coding agent is told to resolve."""
    lines = ["A review of the whole change found these problems. Fix each one, and nothing else:"]
    for f in findings:
        where = f"{f.repo}/{f.file}" if f.repo else f.file
        location = f" ({where}:{f.line})" if where and f.line else f" ({where})" if where else ""
        lines.append(f"{f.number}. [{f.severity}]{location} {f.claim}")
        if f.suggestion:
            lines.append(f"   Suggested fix: {f.suggestion}")
    if notes.strip():
        lines += ["", "The reviewer who asked for this rework added:", notes.strip()]
    return "\n".join(lines)


def make_pr_review(
    reviewer: PiAgentExecutor,
    repo_map: RepoMap,
    workspaces: RunWorkspaces,
    slots: PiSlots,
    *,
    rules: str = "",
    budget_usd: float = 0.0,
) -> AsyncNode:
    cfg = StageModelConfig(provider=reviewer.provider, model=reviewer.model, api_key="")

    async def pr_review(state: GraphState) -> dict:
        issue = state["issue"]
        key = state["issue_key"]
        progress.mark(key, "pr_review")
        done = {"fix_request": None, "status": "pending_final", "current_node": "pr_review"}

        if state.get("pr_review_skip"):
            # Someone retried a stuck review with "skip": the final gate says it was not reviewed.
            logger.warning("PR review of %s skipped on request", key)
            return {**done, "pr_review": None, "pr_review_skipped": True, "pr_review_skip": False}

        base_shas = state.get("base_shas") or {}
        tree_shas = state.get("tree_shas") or {}
        diff = {
            name: {"base": base_shas[name], "tree": tree} for name, tree in tree_shas.items() if base_shas.get(name)
        }
        if not diff:
            return {**done, "pr_review": ReviewResult(summary="The run changed no files."), "pr_review_skipped": False}

        usage.check_budget(state, budget_usd)
        fix = state.get("fix_request") or {}
        repos = selected_repos(repo_map, state)
        logger.info("PR review of %s over %d repo(s)%s", key, len(diff), " (re-review)" if fix else "")
        async with slots.hold(key, "pr_review"):
            result = await reviewer.run_review(
                key,
                issue=issue,
                repo_cwd=workspaces.cwd(key, repos),
                repo_roots=workspaces.roots_payload(key, repos),
                diff=diff,
                rules=rules,
                requirements=state.get("requirements"),
                plan=state.get("plan_result"),
                previous=fix.get("findings"),
                notes=fix.get("notes", ""),
                max_cost_usd=usage.remaining(state, budget_usd),
            )
        logger.info(
            "PR review of %s: %d finding(s), %d not reviewed, %d dropped",
            key,
            len(result.findings),
            len(result.not_reviewed),
            len(result.dropped_findings),
        )
        return {
            **done,
            "pr_review": result,
            "pr_review_skipped": False,
            "usage": usage.appended(state, usage.from_pi("pr_review", cfg, result.usage)),
        }

    return pr_review
