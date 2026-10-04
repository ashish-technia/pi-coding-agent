import logging
from collections.abc import Awaitable, Callable

from ...git_client import GitBranchClient
from ...models import AgentResult, JiraIssue
from ...pi_agent import PiAgentExecutor
from .. import progress
from ..repo_context import RepoMap, describe, repo_roots_payload, selected_repos
from ..state import GraphState

logger = logging.getLogger(__name__)

CodingNode = Callable[[GraphState], Awaitable[dict]]


def phase_plan(plan: AgentResult, mode: str, phase_index: int) -> AgentResult:
    """The slice of the approved plan the coding agent must implement now.

    Phases slice by stage, not by repository: one Pi session edits every selected
    repo in the same pass, so a contract change and its consumer land together.
    """
    if mode != "phased" or not plan.phases:
        return plan
    phase = plan.phases[min(phase_index, len(plan.phases) - 1)]
    steps = [plan.plan_steps[i] for i in phase.step_indexes if 0 <= i < len(plan.plan_steps)]
    if not steps:  # defensive: a phase with no valid indexes falls back to the whole plan
        return plan
    return plan.model_copy(
        update={
            "plan_steps": steps,
            "files_changed": sorted({s.file for s in steps}),
            "analysis": f"{plan.analysis}\n\nCurrent phase: {phase.name}. {phase.description}".strip(),
        }
    )


def make_coding_agent(
    pi_agent: PiAgentExecutor,
    repo_map: RepoMap,
    git_clients: dict[str, GitBranchClient],
    *,
    prepare_branch_before_pr: bool,
) -> CodingNode:
    async def coding_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        progress.mark(issue.key, "coding_agent")
        plan_result: AgentResult = state["plan_result"]
        mode = state.get("execution_mode", "all")
        phase_index = state.get("phase_index", 0)
        iteration = state.get("iteration", 0)
        review_feedback = state.get("review_feedback", "")
        repos = selected_repos(repo_map, state)

        first_pass = phase_index == 0 and iteration == 0
        if prepare_branch_before_pr and first_pass:
            # The same branch name in every repo, so a multi-repo change is one name.
            for repo in repos:
                git_branch = git_clients.get(repo.name)
                if not git_branch:
                    raise RuntimeError(
                        f"Branch preparation is enabled but repo {repo.name!r} has no local path configured."
                    )
                logger.info(
                    "Preparing source branch %s from %s in repo %s for issue %s",
                    plan_result.branch_name,
                    repo.target_branch,
                    repo.name,
                    issue.key,
                )
                git_branch.prepare_branch(
                    target_branch=repo.target_branch,
                    source_branch=plan_result.branch_name,
                    push=False,
                )

        work_order = phase_plan(plan_result, mode, phase_index)
        logger.info(
            "Coding issue %s: repos=%s mode=%s phase=%d/%d iteration=%d steps=%d",
            issue.key,
            describe(repos),
            mode,
            phase_index + 1,
            state.get("phases_total", 1),
            iteration,
            len(work_order.plan_steps),
        )
        code_result: AgentResult = await pi_agent.run_with_mode(
            issue,
            repo_cwd=(repos[0].path if repos else "") or ".",
            repo_roots=repo_roots_payload(repos),
            execute_changes=True,
            branch_name=plan_result.branch_name,
            plan=work_order,
            requirements=state.get("requirements"),
            review_feedback=review_feedback if iteration > 0 else "",
        )
        logger.info("Coding agent completed for issue %s", issue.key)

        # Nothing is committed until pr_node, so each repo's working tree holds every
        # change made for this issue so far. Repos left untouched stay out of the map.
        diffs: dict[str, str] = {}
        for repo in repos:
            git_branch = git_clients.get(repo.name)
            if git_branch and git_branch.has_changes():
                diffs[repo.name] = git_branch.diff()

        return {
            "code_result": code_result,
            "diffs": diffs,
            "status": "reviewing",
            "iteration": iteration + 1,
            "current_node": "coding_agent",
        }

    return coding_agent
