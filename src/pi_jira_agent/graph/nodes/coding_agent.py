import asyncio
import logging

from ... import usage
from ...config import StageModelConfig
from ...git_client import GitBranchClient
from ...models import AgentResult, JiraIssue
from ...pi_agent import PiAgentExecutor
from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, describe, selected_repos
from ..slots import PiSlots
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


def make_coding_agent(
    pi_agent: PiAgentExecutor,
    repo_map: RepoMap,
    workspaces: RunWorkspaces,
    slots: PiSlots,
    *,
    budget_usd: float = 0.0,
) -> CodingNode:
    cfg = StageModelConfig(provider=pi_agent.provider, model=pi_agent.model, api_key="")

    async def coding_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        key = state["issue_key"]
        progress.mark(issue.key, "coding_agent")
        usage.check_budget(state, budget_usd)
        plan_result: AgentResult = state["plan_result"]
        mode = state.get("execution_mode", "all")
        phase_index = state.get("phase_index", 0)
        iteration = state.get("iteration", 0)
        review_feedback = state.get("review_feedback", "")
        repos = selected_repos(repo_map, state)
        git_clients: dict[str, GitBranchClient] = {
            repo.name: workspaces.git(key, repo.name) for repo in repos if repo.path.strip()
        }

        # A coding pass that was cut short (the service went down mid-session) leaves half an
        # edit behind, and the node runs again from the top. Put each worktree back to what
        # the last finished pass left: its snapshot, or the base commit before the first one.
        base_shas = state.get("base_shas") or {}
        last_trees = state.get("tree_shas") or {}

        def discard_leftovers() -> list[str]:
            restored = []
            for name, git_branch in git_clients.items():
                expected = git_branch.tree_of(last_trees.get(name) or base_shas.get(name) or "")
                if expected and git_branch.snapshot() != expected:
                    git_branch.restore(expected)
                    restored.append(name)
            return restored

        restored = await asyncio.to_thread(discard_leftovers)
        if restored:
            logger.warning("Discarded an interrupted coding pass for %s in repo(s) %s", issue.key, ", ".join(restored))
            progress.add_event(
                issue.key,
                {
                    "source": "git",
                    "ev": "restore",
                    "text": f"discarded an interrupted coding pass in {', '.join(restored)}",
                },
            )

        first_pass = phase_index == 0 and iteration == 0
        if first_pass:
            # The worktree was created detached, before the plan named a branch. The same
            # branch name in every repo, so a multi-repo change is one name.
            for name, git_branch in git_clients.items():
                logger.info("Creating branch %s in repo %s for issue %s", plan_result.branch_name, name, issue.key)
                await asyncio.to_thread(git_branch.create_branch, plan_result.branch_name)

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
        async with slots.hold(issue.key, "coding_agent"):
            code_result: AgentResult = await pi_agent.run_with_mode(
                issue,
                repo_cwd=workspaces.cwd(key, repos),
                repo_roots=workspaces.roots_payload(key, repos),
                execute_changes=True,
                branch_name=plan_result.branch_name,
                plan=work_order,
                requirements=state.get("requirements"),
                review_feedback=review_feedback if iteration > 0 else "",
                max_cost_usd=usage.remaining(state, budget_usd),
            )
        logger.info("Coding agent completed for issue %s", issue.key)

        # Nothing is committed until pr_node, so each repo's worktree holds every
        # change made for this issue so far. Repos left untouched stay out of the map.
        # `diffs` is the whole change so far, for the human; `phase_diff` is what this phase
        # added on top of the tree the previous phase ended on, for the reviewer.
        phase_base = state.get("phase_base") or {}
        tree_shas: dict[str, str] = {}
        diffs: dict[str, str] = {}
        phase_diff: dict[str, str] = {}

        def capture() -> None:
            for name, git_branch in git_clients.items():
                if not git_branch.has_changes():
                    continue
                base = base_shas.get(name) or "HEAD"
                tree = git_branch.snapshot(keep_as=workspaces.snapshot_ref(key, phase_index))
                tree_shas[name] = tree
                diffs[name] = git_branch.diff(base=base, tree=tree)
                since_phase_start = git_branch.diff(base=phase_base.get(name) or base, tree=tree)
                if since_phase_start.strip():
                    phase_diff[name] = since_phase_start

        # git runs as a blocking subprocess; on the event loop it would freeze the API and every other run.
        await asyncio.to_thread(capture)

        return {
            "usage": usage.appended(state, usage.from_pi("coding", cfg, code_result.usage)),
            "code_result": code_result,
            "diffs": diffs,
            "phase_diff": phase_diff,
            "tree_shas": tree_shas,
            "status": "reviewing",
            "iteration": iteration + 1,
            "current_node": "coding_agent",
        }

    return coding_agent
