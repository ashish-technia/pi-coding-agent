import asyncio
import logging
import time
from collections.abc import Coroutine

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from .channels.jira_comments import JiraCommentChannel
from .config import settings
from .graph import progress
from .graph.build import build_graph, make_checkpointer, make_jira_client, make_serde, make_workspaces
from .graph.decisions import parse_decision
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


class AutomationService:
    def __init__(self):
        self._graph_builder = build_graph()
        self._checkpointer_cm = None
        self.graph: CompiledStateGraph | None = None
        self._tasks: dict[str, asyncio.Task] = {}
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
        logger.info(
            "AutomationService ready (checkpointer=%s, jira_channel=%s)",
            "postgres" if settings.database_url else "sqlite",
            bool(self.jira_channel),
        )

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await self.registry.close()
        if self._checkpointer_cm:
            await self._checkpointer_cm.__aexit__(None, None, None)

    @property
    def _compiled(self) -> CompiledStateGraph:
        if self.graph is None:
            raise RuntimeError("AutomationService is not started; call start() first.")
        return self.graph

    @staticmethod
    def _config(issue_key: str) -> RunnableConfig:
        return {"configurable": {"thread_id": issue_key}}

    def _run_background(self, issue_key: str, coro: Coroutine) -> None:
        async def runner() -> None:
            try:
                await coro
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
    ) -> dict:
        """Start (or resume) the workflow for a Jira issue key.

        ``repos`` names the repositories this run may work in; None means the repos
        flagged ``default_selected`` in repos.json.
        """
        issue_key = issue_key.strip().upper()
        config = self._config(issue_key)

        if issue_key in self._tasks:
            return await self.get_status(issue_key)

        existing = await self._compiled.aget_state(config)
        if existing and existing.next:
            next_nodes = set(existing.next)
            if next_nodes <= _GATE_NODES:
                # Paused for a human — do not restart.
                return await self.get_status(issue_key)
            if not self._first_task_error(existing):
                logger.info("Auto-resuming %s after restart (next=%s)", issue_key, next_nodes)
                self._run_background(issue_key, self._compiled.ainvoke(None, config=config))
            return await self.get_status(issue_key)

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
        )
        progress.reset_activity(issue_key)
        await self.registry.upsert(
            issue_key, summary=inline_issue.summary if inline_issue else None, status="fetching", channel=channel
        )
        self._run_background(issue_key, self._compiled.ainvoke(state, config=config))
        return await self.get_status(issue_key)

    # Backwards-compatible name used by the legacy webhook path.
    async def process_issue(self, issue: JiraIssue) -> dict:
        return await self.start_run(issue.key, channel="ui", inline_issue=issue)

    async def get_pending(self, issue_key: str) -> dict | None:
        config = self._config(issue_key)
        state = await self._compiled.aget_state(config)
        if not state or not state.tasks:
            return None
        for task in state.tasks:
            for pending_interrupt in task.interrupts:
                return pending_interrupt.value
        return None

    async def submit_decision(self, issue_key: str, payload: dict) -> dict:
        """Validate a human decision against the pending gate and resume the graph."""
        issue_key = issue_key.strip().upper()
        if issue_key in self._tasks:
            raise ValueError(f"{issue_key} is still running; wait for it to pause before deciding.")
        pending = await self.get_pending(issue_key)
        if not pending:
            raise ValueError(f"{issue_key} is not waiting for a decision.")
        pending_type = pending.get("type", "")
        decision = parse_decision(pending_type, payload)
        if pending_type == "final_review" and payload.get("action") == "create_pr" and not pending.get("pr_enabled"):
            raise ValueError("Pull request creation is disabled (PR_CREATION_ENABLED=false).")
        resume = decision.model_dump(mode="json")
        logger.info("Decision for %s at %s: %s", issue_key, pending_type, resume.get("action"))
        self._run_background(issue_key, self._compiled.ainvoke(Command(resume=resume), config=self._config(issue_key)))
        return await self.get_status(issue_key)

    async def submit_comment(self, issue_key: str, body: str, author_account_id: str = "") -> dict:
        """Flow 2: a human replied on the Jira issue. Map the command to a decision."""
        if not self.jira_channel:
            raise ValueError("Jira comment channel is disabled (JIRA_COMMENT_CHANNEL_ENABLED=false).")
        pending = await self.get_pending(issue_key)
        if not pending:
            return {"issue": issue_key, "handled": False, "reason": "no pending decision"}
        decision = self.jira_channel.decision_from_comment(pending.get("type", ""), body, author_account_id)
        if decision is None:
            return {"issue": issue_key, "handled": False, "reason": "comment is not a command"}
        try:
            status = await self.submit_decision(issue_key, decision)
        except ValueError as exc:
            await self.jira_channel.notify_text(issue_key, f"I could not apply that reply: {exc}")
            return {"issue": issue_key, "handled": False, "reason": str(exc)}
        return {"issue": issue_key, "handled": True, "decision": decision, **status}

    async def retry(self, issue_key: str) -> dict:
        """Resume a run stuck on a node that previously raised an exception."""
        config = self._config(issue_key)
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
        pending = None
        for task in state.tasks:
            for pending_interrupt in task.interrupts:
                pending = pending_interrupt.value
                break

        node_since = live_progress["since"] if live_progress else None
        result = {
            "issue": issue_key,
            "issue_details": issue.model_dump() if issue else None,
            "channel": values.get("channel", "ui"),
            "running": is_running,
            "node": node,
            "node_label": node_label,
            "node_elapsed_s": round(time.time() - node_since) if node_since else None,
            "pi_timeout_s": settings.pi_timeout_seconds if node in {"planning_agent", "coding_agent"} else None,
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
            "code_result": _dump(values.get("code_result")),
            "repos": values.get("repos") or [],
            "diffs": values.get("diffs") or {},
            "phase_diffs": values.get("phase_diffs") or [],
            "review_approved": values.get("review_approved"),
            "review_feedback": values.get("review_feedback"),
            "pr_title": values.get("pr_title"),
            "pr_urls": values.get("pr_urls") or {},
        }

        if pending and not is_running:
            result["status"] = _STATUS_FOR_PENDING.get(pending.get("type"), result["status"])

        if state.next and not is_running:
            error = self._first_task_error(state)
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
