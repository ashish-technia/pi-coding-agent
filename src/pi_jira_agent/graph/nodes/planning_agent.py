import logging

from ...models import AgentResult, JiraIssue
from ...pi_agent import PiAgentExecutor
from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, describe, selected_repos
from ..slots import PiSlots
from ..state import AsyncNode, GraphState

logger = logging.getLogger(__name__)

PlanningNode = AsyncNode


def make_planning_agent(
    pi_agent: PiAgentExecutor, repo_map: RepoMap, workspaces: RunWorkspaces, slots: PiSlots
) -> PlanningNode:
    async def planning_agent(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        progress.mark(issue.key, "planning_agent")
        notes = state.get("plan_notes", "")
        # On a refine, hand the planner its previous plan plus the reviewer's notes so it
        # revises incrementally instead of re-exploring the whole repository.
        previous_plan: AgentResult | None = state.get("plan_result") if notes else None
        repos = selected_repos(repo_map, state)

        if notes:
            logger.info("Refining plan for issue %s with reviewer notes", issue.key)
        else:
            logger.info(
                "Planning issue %s against the approved requirements in repo(s) %s",
                issue.key,
                describe(repos),
            )
        async with slots.hold(issue.key, "planning_agent"):
            plan_result: AgentResult = await pi_agent.run_with_mode(
                issue,
                repo_cwd=workspaces.cwd(state["issue_key"], repos),
                repo_roots=workspaces.roots_payload(state["issue_key"], repos),
                execute_changes=False,
                plan=previous_plan,
                requirements=state.get("requirements"),
                reviewer_notes=notes,
            )
        phases_total = len(plan_result.phases) if plan_result.phases else 1
        logger.info("Plan for %s: %d step(s), %d phase(s)", issue.key, len(plan_result.plan_steps), phases_total)
        return {
            "plan_result": plan_result,
            "phases_total": phases_total,
            "plan_notes": "",
            "status": "pending_plan",
            "current_node": "planning_agent",
        }

    return planning_agent
