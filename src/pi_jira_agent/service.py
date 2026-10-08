import asyncio
import collections
import logging
import time
from collections.abc import Coroutine

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from . import usage
from .channels.jira_comments import JiraCommentChannel, approver_account_ids
from .config import settings
from .graph import progress
from .graph.build import build_graph, make_checkpointer, make_jira_client, make_serde, make_workspaces
from .graph.decisions import parse_decision, resolve_fix
from .graph.progress import NODE_LABELS, STAGE_OF_NODE
from .graph.state import initial_state
from .models import JiraIssue
from .registry import make_registry

logger = logging.getLogger(__name__)

# Nodes that pause for human input. A run stopped on one of these is waiting, not broken.
_GATE_NODES = {"await_requirements", "await_plan", "phase_gate", "await_final"}

# A node's state update is only applied when it returns, so while a gate is paused the
# stored status is still the previous node's. Derive the effective status from the interrupt.
_STATUS_FOR_PENDING = {
    "requirements_approval": "pending_requirements",
    "plan_approval": "pending_plan",
    "phase_gate": "pending_phase",
    "final_review": "pending_final",
}

# A run in one of these states is over: its worktrees are removed. A stuck run keeps them for retry.
_ENDED_STATUSES = {"done", "failed", "cancelled"}


# A run cut short by a restart is resumed by the service this many times. After that it
# waits for a person, so a run that takes the process down cannot do so in a loop.
_MAX_AUTO_RESUMES = 1
_INTERRUPTED_MESSAGE = (
    "The run was interrupted (the service stopped while it was working) and has already been "
    "resumed automatically. Retry it to continue from its last checkpoint."
)

# How many comment ids per issue are remembered to spot a redelivered Jira comment.
_SEEN_COMMENTS_PER_ISSUE = 200


class ConflictError(Exception):
    """The request lost a race or answers something that is no longer waiting (HTTP 409)."""


def _pending_of(state) -> dict | None:
    """The pending gate's payload plus ``gate_id``, which is unique to this pause.

    The same kind of gate comes back (requirements after a revise, the phase gate between
    phases), so the type alone cannot tell a decision meant for an earlier pause from one
    meant for this one.
    """
    for task in state.tasks if state else ():
        for pending_interrupt in task.interrupts:
            return {**pending_interrupt.value, "gate_id": pending_interrupt.id}
    return None


class AutomationService:
    def __init__(self):
        self._graph_builder = build_graph()
        self._checkpointer_cm = None
        self.graph: CompiledStateGraph | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        # One lock per issue around every check-then-start, so two requests for the same
        # issue cannot both find it idle and both start it.
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen_comments: dict[str, collections.deque[str]] = {}
        # (issue, gate, author) triples already told they may not answer, so each is told once.
        self._refused: set[tuple[str, str, str]] = set()
        self.registry = make_registry(settings.database_url, settings.graph_checkpoint_db)
        self.workspaces = make_workspaces()
        self.jira_channel: JiraCommentChannel | None = None
        if settings.jira_comment_channel_enabled:
            self.jira_channel = JiraCommentChannel(make_jira_client(), agent_account_id=settings.jira_agent_account_id)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._checkpointer_cm = make_checkpointer()
        checkpointer = await self._checkpointer_cm.__aenter__()
        if hasattr(checkpointer, "setup"):
            await checkpointer.setup()
        checkpointer.serde = make_serde()
        self.graph = self._graph_builder.compile(checkpointer=checkpointer)
        await self.registry.setup()
        await self._resume_interrupted()
        logger.info(
            "AutomationService ready (checkpointer=%s, jira_channel=%s)",
            "postgres" if settings.database_url else "sqlite",
            bool(self.jira_channel),
        )

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        # Let them unwind before the checkpointer closes underneath them.
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.close()
        if self._checkpointer_cm:
            await self._checkpointer_cm.__aexit__(None, None, None)

    @property
    def _compiled(self) -> CompiledStateGraph:
        if self.graph is None:
            raise RuntimeError("AutomationService is not started; call start() first.")
        return self.graph

    def _lock(self, issue_key: str) -> asyncio.Lock:
        return self._locks.setdefault(issue_key, asyncio.Lock())

    @staticmethod
    def _config(issue_key: str) -> RunnableConfig:
        return {"configurable": {"thread_id": issue_key}}

    def _run_background(self, issue_key: str, coro: Coroutine) -> None:
        async def runner() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                # Shutdown. The run is interrupted, not paused or failed: record nothing, so
                # the next start finds it exactly as a crash would have left it.
                self._tasks.pop(issue_key, None)
                progress.clear(issue_key)
                raise
            except Exception:
                logger.exception("Background graph run failed for issue %s", issue_key)
            finally:
                # Before the run stops counting as running, so a restart of the same issue
                # cannot race the cleanup of its previous worktrees.
                await self._release_workspace(issue_key)
                self._tasks.pop(issue_key, None)
                progress.clear(issue_key)
                await self._after_run(issue_key)

        self._tasks[issue_key] = asyncio.create_task(runner())

    @staticmethod
    def _interrupted(state) -> bool:
        """True when a run has a node to run next but nothing explains why it is not running:
        no gate is waiting for an answer and no node raised. The process stopped under it."""
        return (
            bool(state and state.next) and _pending_of(state) is None and not AutomationService._first_task_error(state)
        )

    async def _resume_interrupted(self) -> None:
        """On startup, pick up the runs a restart cut short. Runs waiting at a gate are left alone."""
        for row in await self.registry.list(limit=1000):
            issue_key = row["issue_key"]
            if row.get("status") in _ENDED_STATUSES:
                continue
            try:
                config = self._config(issue_key)
                async with self._lock(issue_key):
                    state = await self._compiled.aget_state(config)
                    if issue_key in self._tasks or not self._interrupted(state):
                        continue
                    resumes = state.values.get("auto_resumes") or 0
                    if resumes >= _MAX_AUTO_RESUMES:
                        logger.warning(
                            "%s was interrupted again after an automatic resume; leaving it for a retry.", issue_key
                        )
                        continue
                    logger.info("Resuming %s after a restart (next=%s)", issue_key, list(state.next))
                    await self._compiled.aupdate_state(config, {"auto_resumes": resumes + 1})
                    self._run_background(issue_key, self._compiled.ainvoke(None, config=config))
            except Exception:  # noqa: BLE001
                logger.exception("Could not resume %s after a restart", issue_key)

    async def _release_workspace(self, issue_key: str, *, force: bool = False) -> None:
        """Remove the run's worktrees and local branch once it has ended (or always, with ``force``)."""
        try:
            state = await self._compiled.aget_state(self._config(issue_key))
            values = state.values if state else {}
            ended = bool(values) and not state.next and values.get("status") in _ENDED_STATUSES
            if not (ended or force):
                return
            plan = values.get("plan_result")
            branch = plan.branch_name if plan else None
            await asyncio.to_thread(self.workspaces.remove, issue_key, branch=branch)
        except Exception:  # noqa: BLE001
            logger.exception("Could not release the workspace of %s", issue_key)

    async def _after_run(self, issue_key: str) -> None:
        """Bookkeeping once a background run stops (paused at a gate, finished, or errored)."""
        try:
            status = await self.get_status(issue_key)
            await self.registry.upsert(
                issue_key,
                summary=(status.get("issue_details") or {}).get("summary") or None,
                status=status.get("status") or None,
            )
            pending = status.get("pending")
            if pending and self.jira_channel and status.get("channel") == "jira":
                await self.jira_channel.notify_pending(issue_key, pending)
            elif status.get("status") == "stuck_error" and self.jira_channel and status.get("channel") == "jira":
                await self.jira_channel.notify_text(
                    issue_key,
                    f"The run hit an error and is paused: {status.get('error')}\nAn operator can retry it from the UI.",
                )
        except Exception:  # noqa: BLE001
            logger.exception("Post-run bookkeeping failed for %s", issue_key)

    # ------------------------------------------------------------------ entry points
    async def start_run(
        self,
        issue_key: str,
        *,
        channel: str = "ui",
        inline_issue: JiraIssue | None = None,
        repos: list[str] | None = None,
        pr_review: bool | None = None,
    ) -> dict:
        """Start (or resume) the workflow for a Jira issue key.

        ``repos`` names the repositories this run may work in; None means the repos
        flagged ``default_selected`` in repos.json. ``pr_review`` switches the whole-change
        review before the final gate on or off; None means ``PR_REVIEW_DEFAULT``.
        """
        issue_key = issue_key.strip().upper()
        config = self._config(issue_key)

        async with self._lock(issue_key):
            await self._start_locked(
                issue_key, config, channel=channel, inline_issue=inline_issue, repos=repos, pr_review=pr_review
            )
        return await self.get_status(issue_key)

    async def _start_locked(
        self,
        issue_key: str,
        config: RunnableConfig,
        *,
        channel: str,
        inline_issue: JiraIssue | None,
        repos: list[str] | None,
        pr_review: bool | None = None,
    ) -> None:
        if issue_key in self._tasks:
            return

        existing = await self._compiled.aget_state(config)
        if existing and existing.next:
            # Unfinished: paused for a human, stuck on an error (that is what retry is for), or
            # cut short by a restart. Only the last one is continued here, because someone asked.
            if self._interrupted(existing):
                logger.info("Resuming interrupted run %s (next=%s)", issue_key, list(existing.next))
                self._run_background(issue_key, self._compiled.ainvoke(None, config=config))
            return

        selected = settings.selected_repos(repos)
        # Leftovers of an earlier run of this issue (a crash before cleanup) must not be reused.
        await self._release_workspace(issue_key, force=True)
        logger.info("Starting %s in repo(s) %s", issue_key, ", ".join(r.name for r in selected))
        state = initial_state(
            issue_key,
            channel=channel,
            repos=[r.name for r in selected],
            max_iterations=settings.review_max_iterations,
            issue=inline_issue,
            pr_review=settings.pr_review_default if pr_review is None else pr_review,
        )
        progress.reset_activity(issue_key)
        await self.registry.upsert(
            issue_key, summary=inline_issue.summary if inline_issue else None, status="fetching", channel=channel
        )
        self._run_background(issue_key, self._compiled.ainvoke(state, config=config))

    # Backwards-compatible name used by the legacy webhook path.
    async def process_issue(self, issue: JiraIssue) -> dict:
        return await self.start_run(issue.key, channel="ui", inline_issue=issue)

    async def get_pending(self, issue_key: str) -> dict | None:
        return _pending_of(await self._compiled.aget_state(self._config(issue_key)))

    async def submit_decision(self, issue_key: str, payload: dict, *, decided_by: str = "ui") -> dict:
        """Validate a human decision against the pending gate and resume the graph.

        ``gate_id`` in the payload names the pause being answered. The HTTP API requires it;
        when present it must match, so a stale or repeated decision cannot answer a later gate.
        """
        issue_key = issue_key.strip().upper()
        payload = dict(payload)
        gate_id = payload.pop("gate_id", None)
        async with self._lock(issue_key):
            if issue_key in self._tasks:
                raise ConflictError(f"{issue_key} is still running; wait for it to pause before deciding.")
            pending = await self.get_pending(issue_key)
            if not pending:
                raise ValueError(f"{issue_key} is not waiting for a decision.")
            if gate_id and gate_id != pending["gate_id"]:
                raise ConflictError(
                    f"That decision answers an earlier gate of {issue_key}; reload to see what it is waiting for now."
                )
            pending_type = pending.get("type", "")
            decision = parse_decision(pending_type, payload)
            if (
                pending_type == "final_review"
                and payload.get("action") == "create_pr"
                and not pending.get("pr_enabled")
            ):
                raise ValueError("Pull request creation is disabled (PR_CREATION_ENABLED=false).")
            resume = decision.model_dump(mode="json")
            if resume.get("action") == "fix":
                resume = resolve_fix(resume, pending.get("pr_review") or {})
            resume["decided_by"] = decided_by  # set here, so a caller cannot claim to be someone else
            logger.info("Decision for %s at %s: %s", issue_key, pending_type, resume.get("action"))
            self._run_background(
                issue_key, self._compiled.ainvoke(Command(resume=resume), config=self._config(issue_key))
            )
        return await self.get_status(issue_key)

    async def submit_comment(
        self, issue_key: str, body: str, author_account_id: str = "", comment_id: str = ""
    ) -> dict:
        """Flow 2: a human replied on the Jira issue. Map the command to a decision."""
        if not self.jira_channel:
            raise ValueError("Jira comment channel is disabled (JIRA_COMMENT_CHANNEL_ENABLED=false).")
        if comment_id:
            # Jira Automation can deliver one comment more than once. The second "/approve"
            # would otherwise answer whichever gate the first one led to.
            seen = self._seen_comments.setdefault(issue_key, collections.deque(maxlen=_SEEN_COMMENTS_PER_ISSUE))
            if comment_id in seen:
                return {"issue": issue_key, "handled": False, "reason": "comment was already handled"}
            seen.append(comment_id)
        pending = await self.get_pending(issue_key)
        if not pending:
            return {"issue": issue_key, "handled": False, "reason": "no pending decision"}
        decision = self.jira_channel.decision_from_comment(pending.get("type", ""), body, author_account_id)
        if decision is None:
            return {"issue": issue_key, "handled": False, "reason": "comment is not a command"}
        if not await self._may_answer(issue_key, pending["gate_id"], author_account_id):
            return {"issue": issue_key, "handled": False, "reason": "author is not allowed to answer this gate"}
        try:
            # The command was read against this gate, so it may only answer this gate.
            status = await self.submit_decision(
                issue_key,
                {**decision, "gate_id": pending["gate_id"]},
                decided_by=f"jira:{author_account_id}",
            )
        except (ValueError, ConflictError) as exc:
            await self.jira_channel.notify_text(issue_key, f"I could not apply that reply: {exc}")
            return {"issue": issue_key, "handled": False, "reason": str(exc)}
        return {"issue": issue_key, "handled": True, "decision": decision, **status}

    async def _may_answer(self, issue_key: str, gate_id: str, author_account_id: str) -> bool:
        """Whether a Jira commenter is on the approver list (GATE_APPROVERS) for this issue.

        Someone who is not gets one reply per gate saying so; further commands from them
        at that gate are dropped silently.
        """
        state = await self._compiled.aget_state(self._config(issue_key))
        allowed = approver_account_ids(settings.gate_approver_rules(), state.values.get("issue") if state else None)
        if author_account_id and author_account_id in allowed:
            return True
        logger.warning("Ignored a gate reply on %s from %r: not an approver", issue_key, author_account_id)
        refusal = (issue_key, gate_id, author_account_id)
        if self.jira_channel and refusal not in self._refused:
            self._refused.add(refusal)
            who = " or ".join(settings.gate_approver_rules()) or "nobody"
            await self.jira_channel.notify_text(
                issue_key, f"I did not apply that reply: only {who} may answer this run's gates."
            )
        return False

    async def retry(self, issue_key: str, *, skip_pr_review: bool = False) -> dict:
        """Resume a run stuck on a node that previously raised an exception.

        ``skip_pr_review`` continues a run stuck on the PR review without it, so a model
        outage cannot hold a finished change hostage. The final gate then says so.
        """
        config = self._config(issue_key)
        async with self._lock(issue_key):
            if issue_key in self._tasks:
                raise ConflictError(f"{issue_key} is running; there is nothing to retry.")
            state = await self._compiled.aget_state(config)
            if not state:
                raise RuntimeError(f"No run found for {issue_key}")

            error = self._first_task_error(state)
            stuck_nodes = set(state.next) if state.next else set()
            retry_count = (state.values.get("retry_count") or 0) + 1
            updates: dict = {"retry_count": retry_count}
            if error and "review_agent" in stuck_nodes:
                existing_feedback = state.values.get("review_feedback", "")
                updates["review_feedback"] = f"{existing_feedback}\n[Previous attempt errored: {error[:300]}]".strip()
            if skip_pr_review:
                if "pr_review" not in stuck_nodes:
                    raise ValueError(f"{issue_key} is not stuck on the PR review, so there is nothing to skip.")
                updates["pr_review_skip"] = True
            await self._compiled.aupdate_state(config, updates)
            self._run_background(issue_key, self._compiled.ainvoke(None, config=config))
        return await self.get_status(issue_key)

    async def list_runs(self, limit: int = 50) -> list[dict]:
        rows = await self.registry.list(limit)
        for row in rows:
            row["running"] = row["issue_key"] in self._tasks
        return rows

    # ------------------------------------------------------------------ status
    async def get_status(self, issue_key: str) -> dict:
        issue_key = issue_key.strip().upper()
        config = self._config(issue_key)
        is_running = issue_key in self._tasks
        live_progress = progress.get(issue_key)

        state = await self._compiled.aget_state(config)
        if not state or not state.values:
            return {
                "issue": issue_key,
                "running": is_running,
                "node": live_progress["node"] if live_progress else None,
                "node_label": live_progress["label"] if live_progress else None,
                "stage": None,
                "status": "not_started",
                "pending": None,
                "repos": [],
                "diffs": {},
                "pr_urls": {},
            }

        values = state.values
        checkpoint_node = values.get("current_node")
        node = live_progress["node"] if live_progress else checkpoint_node
        if not is_running and state.next:
            # Paused (gate) or stuck: report the node that will run next.
            node = next(iter(state.next))
        if live_progress:
            node_label = live_progress["label"]
        elif is_running:
            node_label = "Resuming…"  # decision accepted, next node not marked yet
        else:
            node_label = NODE_LABELS.get(node, node) if node else None

        issue = values.get("issue")
        pending = _pending_of(state)

        node_since = live_progress["since"] if live_progress else None
        result = {
            "issue": issue_key,
            "issue_details": issue.model_dump() if issue else None,
            "channel": values.get("channel", "ui"),
            "running": is_running,
            "node": node,
            "node_label": node_label,
            "node_elapsed_s": round(time.time() - node_since) if node_since else None,
            "pi_timeout_s": (
                settings.pi_timeout_seconds if node in {"planning_agent", "coding_agent", "pr_review"} else None
            ),
            "activity": progress.events(issue_key, limit=150),
            "stage": STAGE_OF_NODE.get(node) if node else None,
            "status": values.get("status"),
            "pending": pending if not is_running else None,
            "requirements": _dump(values.get("requirements")),
            "requirements_original": _dump(values.get("requirements_original")),
            "scope_check": _dump(values.get("scope_check")),
            "plan_result": _dump(values.get("plan_result")),
            "execution_mode": values.get("execution_mode"),
            "phase_index": values.get("phase_index"),
            "phases_total": values.get("phases_total"),
            "iteration": values.get("iteration"),
            "max_iterations": values.get("max_iterations"),
            "retry_count": values.get("retry_count", 0),
            "auto_resumes": values.get("auto_resumes") or 0,
            "decision_log": values.get("decision_log") or [],
            "usage": usage.summarise(values.get("usage") or [], settings.run_budget_usd),
            "code_result": _dump(values.get("code_result")),
            "repos": values.get("repos") or [],
            "base_shas": values.get("base_shas") or {},
            "diffs": values.get("diffs") or {},
            "phase_diffs": values.get("phase_diffs") or [],
            "review_approved": values.get("review_approved"),
            "review_feedback": values.get("review_feedback"),
            "pr_review_enabled": bool(values.get("pr_review_enabled")),
            "pr_review": _dump(values.get("pr_review")),
            "pr_review_skipped": bool(values.get("pr_review_skipped")),
            "fix_rounds": values.get("fix_rounds") or 0,
            "pr_title": values.get("pr_title"),
            "pr_urls": values.get("pr_urls") or {},
        }

        if pending and not is_running:
            result["status"] = _STATUS_FOR_PENDING.get(pending.get("type", ""), result["status"])

        if state.next and not is_running:
            # Not running although a node is due: either that node raised, or the process
            # stopped under the run and its automatic resume is used up (or not yet started).
            error = self._first_task_error(state) or (_INTERRUPTED_MESSAGE if self._interrupted(state) else None)
            if error:
                result["status"] = "stuck_error"
                result["error"] = error
                result["stuck_on"] = list(state.next)
                result["pending"] = None

        return result

    @staticmethod
    def _first_task_error(state) -> str | None:
        for task in state.tasks:
            if task.error:
                return str(task.error)
        return None


def _dump(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value
