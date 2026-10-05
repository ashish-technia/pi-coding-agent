import logging

from ...git_client import GitBranchClient
from ...models import AgentResult, JiraIssue
from ...pi_agent import PiAgentExecutor
from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, describe, selected_repos
from ..state import AsyncNode, GraphState

logger = logging.getLogger(__name__)

CodingNode = AsyncNode


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


def make_coding_agent(pi_agent: PiAgentExecutor, repo_map: RepoMap, workspaces: RunWorkspaces) -> CodingNode:
    async def coding_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        key = state["issue_key"]
        progress.mark(issue.key, "coding_agent")
        plan_result: AgentResult = state["plan_result"]
        mode = state.get("execution_mode", "all")
        phase_index = state.get("phase_index", 0)
        iteration = state.get("iteration", 0)
        review_feedback = state.get("review_feedback", "")
        repos = selected_repos(repo_map, state)
        git_clients: dict[str, GitBranchClient] = {
            repo.name: workspaces.git(key, repo.name) for repo in repos if repo.path.strip()
        }

        first_pass = phase_index == 0 and iteration == 0
        if first_pass:
            # The worktree was created detached, before the plan named a branch. The same
            # branch name in every repo, so a multi-repo change is one name.
            for name, git_branch in git_clients.items():
                logger.info("Creating branch %s in repo %s for issue %s", plan_result.branch_name, name, issue.key)
                git_branch.create_branch(plan_result.branch_name)

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
            repo_cwd=workspaces.cwd(key, repos),
            repo_roots=workspaces.roots_payload(key, repos),
            execute_changes=True,
            branch_name=plan_result.branch_name,
            plan=work_order,
            requirements=state.get("requirements"),
            review_feedback=review_feedback if iteration > 0 else "",
        )
        logger.info("Coding agent completed for issue %s", issue.key)

        # Nothing is committed until pr_node, so each repo's worktree holds every
        # change made for this issue so far. Repos left untouched stay out of the map.
        diffs: dict[str, str] = {}
        for name, git_branch in git_clients.items():
            if git_branch.has_changes():
                diffs[name] = git_branch.diff()

        return {
            "code_result": code_result,
            "diffs": diffs,
            "status": "reviewing",
            "iteration": iteration + 1,
            "current_node": "coding_agent",
        }

    return coding_agent
