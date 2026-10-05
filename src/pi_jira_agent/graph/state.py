from collections.abc import Coroutine
from typing import Any, Literal, Protocol, TypedDict

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
    error: str | None


class AsyncNode(Protocol):
    """An async graph node. The parameter must be named ``state`` for LangGraph's StateNode."""

    def __call__(self, state: GraphState) -> Coroutine[Any, Any, dict]: ...
