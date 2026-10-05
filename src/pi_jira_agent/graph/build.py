import logging
from pathlib import Path

from langgraph.graph import END, StateGraph

from ..bitbucket_client import BitbucketClient
from ..config import settings
from ..jira_client import JiraClient
from ..llm import make_chat_model
from ..pi_agent import PiAgentExecutor
from ..workspace import RunWorkspaces
from . import progress
from .nodes.coding_agent import make_coding_agent
from .nodes.fetch_issue import make_fetch_issue
from .nodes.gates import await_plan, await_requirements, make_await_final, phase_gate
from .nodes.planning_agent import make_planning_agent
from .nodes.pr_node import make_pr_node
from .nodes.requirements_agent import make_requirements_agent, make_scope_check
from .nodes.review_agent import make_review_agent
from .nodes.workspace import make_prepare_workspace
from .orchestrator import (
    route_after_final_gate,
    route_after_phase_gate,
    route_after_plan_gate,
    route_after_requirements_gate,
    route_after_review,
    route_after_scope_check,
)
from .slots import PiSlots
from .state import GraphState

logger = logging.getLogger(__name__)


def _make_cancelled_node(jira: JiraClient, *, comments_enabled: bool):
    async def cancelled_node(state: GraphState) -> dict:
        progress.mark(state["issue"].key, "cancelled_node")
        if comments_enabled:
            await jira.add_comment(state["issue"].key, "Automation cancelled by reviewer.")
        return {"status": "cancelled", "current_node": "cancelled_node"}

    return cancelled_node


def _make_failed_node(jira: JiraClient, *, comments_enabled: bool):
    async def failed_node(state: GraphState) -> dict:
        progress.mark(state["issue"].key, "failed_node")
        feedback = state.get("review_feedback", "")
        if comments_enabled:
            await jira.add_comment(
                state["issue"].key,
                f"Automation failed review after {state.get('iteration', 0)} attempt(s). "
                f"Last review feedback: {feedback}",
            )
        return {"status": "failed", "current_node": "failed_node"}

    return failed_node


def make_pi_executor(stage: str) -> PiAgentExecutor:
    cfg = settings.stage_model(stage)  # type: ignore[arg-type]
    return PiAgentExecutor(
        model=cfg.model,
        system_prompt=settings.pi_system_prompt,
        api_key=cfg.api_key,
        provider=cfg.provider,
        node_command=settings.pi_node_command,
        runner_script=settings.pi_runner_script,
        agent_dir=settings.pi_agent_dir,
        timeout_seconds=settings.pi_timeout_seconds,
        thinking_level=settings.pi_thinking_level,
    )


# Kept for callers/tests that patch the review model factory.
def make_review_llm():
    return make_chat_model(settings.stage_model("review"))


def make_requirements_llm():
    return make_chat_model(settings.stage_model("requirements"))


def make_jira_client() -> JiraClient:
    return JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
    )


def make_workspaces() -> RunWorkspaces:
    """Where each run's worktrees live. Stateless, so the graph and the service build their own."""
    return RunWorkspaces(
        {r.name: r for r in settings.repos()},
        runs_root=settings.runs_root,
        remote_name=settings.git_remote_name,
    )


def build_graph():
    jira = make_jira_client()

    # Everything per *configured* repo is built once here. A run picks a subset by name
    # (GraphState["repos"]), so editing repos.json needs a restart to take effect.
    repos = settings.repos()
    repo_map = {r.name: r for r in repos}
    workspaces = make_workspaces()
    slots = PiSlots(settings.max_concurrent_runs)
    bitbucket_clients = {
        r.name: BitbucketClient(
            base_url=settings.bitbucket_base_url,
            workspace=settings.bitbucket_workspace,
            repo_slug=r.bitbucket_repo_slug,
            username=settings.bitbucket_username,
            app_password=settings.bitbucket_app_password,
            token=settings.bitbucket_token,
        )
        for r in repos
        if r.bitbucket_repo_slug.strip()
    }
    logger.info("Configured repos: %s", ", ".join(repo_map) or "(none)")

    requirements_llm = make_requirements_llm()
    review_llm = make_review_llm()
    planner = make_pi_executor("planning")
    coder = make_pi_executor("coding")

    graph = StateGraph(GraphState)
    graph.add_node("fetch_issue", make_fetch_issue(jira))
    graph.add_node("requirements_agent", make_requirements_agent(requirements_llm))
    graph.add_node("await_requirements", await_requirements)
    graph.add_node("scope_check", make_scope_check(requirements_llm))
    graph.add_node("prepare_workspace", make_prepare_workspace(workspaces, repo_map))
    graph.add_node("planning_agent", make_planning_agent(planner, repo_map, workspaces, slots))
    graph.add_node("await_plan", await_plan)
    graph.add_node("coding_agent", make_coding_agent(coder, repo_map, workspaces, slots))
    graph.add_node("review_agent", make_review_agent(review_llm, review_rules=settings.review_rules()))
    graph.add_node("phase_gate", phase_gate)
    graph.add_node("await_final", make_await_final(pr_enabled=settings.pr_enabled))
    graph.add_node(
        "pr_node",
        make_pr_node(
            bitbucket_clients,
            jira,
            workspaces,
            repo_map,
            jira_transition_done_id=settings.jira_transition_done_id,
            comments_enabled=settings.jira_comments_enabled,
        ),
    )
    graph.add_node("cancelled_node", _make_cancelled_node(jira, comments_enabled=settings.jira_comments_enabled))
    graph.add_node("failed_node", _make_failed_node(jira, comments_enabled=settings.jira_comments_enabled))

    graph.set_entry_point("fetch_issue")
    graph.add_edge("fetch_issue", "requirements_agent")
    graph.add_edge("requirements_agent", "await_requirements")
    graph.add_conditional_edges(
        "await_requirements",
        route_after_requirements_gate,
        {
            "scope_check": "scope_check",
            "requirements_agent": "requirements_agent",
            "cancelled_node": "cancelled_node",
        },
    )
    graph.add_conditional_edges(
        "scope_check",
        route_after_scope_check,
        {"await_requirements": "await_requirements", "prepare_workspace": "prepare_workspace"},
    )
    graph.add_edge("prepare_workspace", "planning_agent")
    graph.add_edge("planning_agent", "await_plan")
    graph.add_conditional_edges(
        "await_plan",
        route_after_plan_gate,
        {
            "coding_agent": "coding_agent",
            "planning_agent": "planning_agent",
            "cancelled_node": "cancelled_node",
        },
    )
    graph.add_edge("coding_agent", "review_agent")
    graph.add_conditional_edges(
        "review_agent",
        route_after_review,
        {"phase_gate": "phase_gate", "coding_agent": "coding_agent", "failed_node": "failed_node"},
    )
    graph.add_conditional_edges(
        "phase_gate",
        route_after_phase_gate,
        {"coding_agent": "coding_agent", "await_final": "await_final"},
    )
    graph.add_conditional_edges(
        "await_final",
        route_after_final_gate,
        {"pr_node": "pr_node", "__end__": END},
    )
    graph.add_edge("pr_node", END)
    graph.add_edge("cancelled_node", END)
    graph.add_edge("failed_node", END)

    return graph


def make_serde():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("pi_jira_agent.models", "JiraIssue"),
            ("pi_jira_agent.models", "JiraComment"),
            ("pi_jira_agent.models", "AgentResult"),
            ("pi_jira_agent.models", "PlanStep"),
            ("pi_jira_agent.models", "PlanPhase"),
            ("pi_jira_agent.models", "RequirementsSpec"),
            ("pi_jira_agent.models", "ScopeCheck"),
            ("pi_jira_agent.models", "ScopeFinding"),
        ],
    )


def make_checkpointer():
    """Postgres when DATABASE_URL is set (multi-worker, restart-safe), else a SQLite file."""
    if settings.database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        return AsyncPostgresSaver.from_conn_string(settings.database_url)

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = Path(settings.graph_checkpoint_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(db_path))
