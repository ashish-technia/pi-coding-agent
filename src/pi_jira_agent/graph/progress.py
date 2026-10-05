"""In-process, sub-checkpoint progress: which node is running and what the agents are doing.

Two stores, both keyed by issue key and bounded:
- ``_progress``: the node currently executing and when it started.
- ``_activity``: a ring buffer of recent agent events (tool calls, model turns, LLM
  calls, validation rounds) streamed from the Pi runner and the LangChain stages.
"""

import collections
import itertools
import threading
import time

_progress: dict[str, dict] = {}
_activity: dict[str, collections.deque] = {}
_seq = itertools.count(1)
_lock = threading.Lock()

ACTIVITY_LIMIT = 400

NODE_LABELS = {
    "fetch_issue": "Fetching Jira issue",
    "requirements_agent": "Framing requirements",
    "await_requirements": "Awaiting requirements approval",
    "scope_check": "Checking scope of your edits",
    "prepare_workspace": "Preparing the worktree",
    "planning_agent": "Planning (reading the code)",
    "await_plan": "Awaiting plan approval",
    "coding_agent": "Coding",
    "review_agent": "Reviewing",
    "phase_gate": "Awaiting phase decision",
    "await_final": "Awaiting final review",
    "pr_node": "Creating pull request",
    "cancelled_node": "Cancelling",
    "failed_node": "Recording failure",
}

# Order used by the UI stepper. Gates map onto the stage they guard.
STAGE_OF_NODE = {
    "fetch_issue": "requirements",
    "requirements_agent": "requirements",
    "await_requirements": "requirements",
    "scope_check": "requirements",
    "prepare_workspace": "plan",
    "planning_agent": "plan",
    "await_plan": "plan",
    "coding_agent": "code",
    "review_agent": "review",
    "phase_gate": "review",
    "await_final": "final",
    "pr_node": "pr",
    "cancelled_node": "final",
    "failed_node": "review",
}


def mark(issue_key: str, node: str) -> None:
    _progress[issue_key] = {
        "node": node,
        "label": NODE_LABELS.get(node, node),
        "since": time.time(),
    }
    add_event(issue_key, {"ev": "node", "node": node, "label": NODE_LABELS.get(node, node)})


def get(issue_key: str) -> dict | None:
    return _progress.get(issue_key)


def clear(issue_key: str) -> None:
    _progress.pop(issue_key, None)


def add_event(issue_key: str, event: dict) -> None:
    """Record one activity event (thread-safe; the runner reader runs in a worker thread)."""
    with _lock:
        buf = _activity.get(issue_key)
        if buf is None:
            buf = _activity[issue_key] = collections.deque(maxlen=ACTIVITY_LIMIT)
        buf.append({"seq": next(_seq), "at": time.time(), **event})


def events(issue_key: str, *, after_seq: int = 0, limit: int = 200) -> list[dict]:
    with _lock:
        buf = _activity.get(issue_key)
        if not buf:
            return []
        items = [e for e in buf if e["seq"] > after_seq]
    return items[-limit:]


def reset_activity(issue_key: str) -> None:
    with _lock:
        _activity.pop(issue_key, None)
