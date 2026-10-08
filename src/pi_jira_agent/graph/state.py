from collections.abc import Coroutine
from typing import Any, Literal, Protocol, TypedDict, cast

from ..models import AgentResult, JiraIssue, RequirementsSpec, ScopeCheck

Status = Literal[
    "fetching",
    "framing_requirements",
    "pending_requirements",
    "planning",
    "pending_plan",
    "coding",
    "reviewing",
    "pending_phase",
    "pending_final",
    "creating_pr",
    "done",
    "failed",
    "cancelled",
]

ExecutionMode = Literal["all", "phased"]
Channel = Literal["ui", "jira"]


class GraphState(TypedDict, total=False):
    # --- Input ---------------------------------------------------------
    issue_key: str
    issue: JiraIssue
    channel: Channel  # where feedback requests are surfaced (UI now, Jira comments in flow 2)
    repos: list[str]  # repo names this run works in, in repos.json order; [0] is the primary
    base_shas: dict[str, str]  # repo name -> commit the run's worktree was created from

    # --- Requirements stage ----------------------------------------------
    requirements_original: RequirementsSpec  # the agent's framing, kept for scope comparison
    requirements: RequirementsSpec  # current version (may include human edits)
    requirements_notes: str  # human "revise" notes for the next framing pass
    scope_check: ScopeCheck | None  # findings for the latest human edit
    scope_acknowledged: bool  # human chose to keep out-of-scope items

    # --- Plan stage ------------------------------------------------------
    plan_result: AgentResult
    plan_notes: str  # human "refine" notes for the next planning pass
    execution_mode: ExecutionMode
    phase_index: int  # 0-based phase currently being implemented
    phases_total: int

    # --- Coding / review loop -------------------------------------------
    code_result: AgentResult
    diffs: dict[str, str]  # repo name -> cumulative worktree diff; repos with no changes are absent
    phase_diff: dict[str, str]  # repo name -> what the current phase changed; this is what the reviewer sees
    phase_diffs: list[dict[str, str]]  # one `phase_diff` per accepted phase
    tree_shas: dict[str, str]  # repo name -> tree snapshot of the worktree after the last coding pass
    phase_base: dict[str, str]  # repo name -> tree the current phase started from; empty in the first phase
    review_approved: bool
    review_feedback: str
    review_omitted_files: list[str]  # changed files the last review did not see (diff over the size limit)
    iteration: int  # coding→review cycles within the current phase
    max_iterations: int

    # --- Delivery --------------------------------------------------------
    pr_title: str
    pr_urls: dict[str, str]  # repo name -> pull request URL, one per repo that had changes

    # --- Bookkeeping -----------------------------------------------------
    status: Status
    current_node: str | None  # persisted so restarts can show where the run is
    retry_count: int  # error-based retries (distinct from the review loop)
    auto_resumes: int  # times the service resumed this run by itself after a restart
    decision_log: list[dict]  # one entry per answered gate: gate, action, by, at
    usage: list[dict]  # one entry per model call that returned: stage, model, tokens, cost_usd
    error: str | None


def initial_state(
    issue_key: str,
    *,
    channel: str,
    repos: list[str],
    max_iterations: int,
    issue: JiraIssue | None = None,
) -> GraphState:
    """The input a new run starts from: a value for **every** field.

    A run's checkpoint thread is its issue key, so starting a finished issue again reuses
    the thread, and LangGraph keeps any key the new input leaves out. Spelling out the
    whole state is what stops the previous run's plan, scope findings or PR URLs from
    showing up in the next one. `tests/test_state.py` fails when a field is missing here.
    """
    return cast(
        GraphState,
        {
            "issue_key": issue_key,
            "issue": issue,  # None: fetch_issue reads it from Jira
            "channel": channel,
            "repos": repos,
            "base_shas": {},
            "requirements_original": None,
            "requirements": None,
            "requirements_notes": "",
            "scope_check": None,
            "scope_acknowledged": False,
            "plan_result": None,
            "plan_notes": "",
            "execution_mode": "all",
            "phase_index": 0,
            "phases_total": 1,
            "code_result": None,
            "diffs": {},
            "phase_diff": None,
            "phase_diffs": [],
            "tree_shas": {},
            "phase_base": {},
            "review_approved": None,
            "review_feedback": "",
            "review_omitted_files": [],
            "iteration": 0,
            "max_iterations": max_iterations,
            "pr_title": "",
            "pr_urls": {},
            "status": "fetching",
            "current_node": None,
            "retry_count": 0,
            "auto_resumes": 0,
            "decision_log": [],
            "usage": [],
            "error": None,
        },
    )


class AsyncNode(Protocol):
    """An async graph node. The parameter must be named ``state`` for LangGraph's StateNode."""

    def __call__(self, state: GraphState) -> Coroutine[Any, Any, dict]: ...
