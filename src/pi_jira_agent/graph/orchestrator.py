"""Conditional-edge routers. Each reads the status a gate/node just wrote."""

import logging

from ..config import settings
from .state import GraphState

logger = logging.getLogger(__name__)


def _log(state: GraphState, src: str, dest: str) -> str:
    logger.info("[orchestrator] %s: %s -> %s", state["issue"].key, src, dest)
    return dest


def route_after_requirements_gate(state: GraphState) -> str:
    status = state.get("status")
    if status == "planning":
        return _log(state, "await_requirements", "scope_check")
    if status == "cancelled":
        return _log(state, "await_requirements", "cancelled_node")
    return _log(state, "await_requirements", "requirements_agent")


def route_after_scope_check(state: GraphState) -> str:
    if state.get("status") == "pending_requirements":
        return _log(state, "scope_check", "await_requirements")
    return _log(state, "scope_check", "planning_agent")


def route_after_plan_gate(state: GraphState) -> str:
    status = state.get("status")
    if status == "coding":
        return _log(state, "await_plan", "coding_agent")
    if status == "cancelled":
        return _log(state, "await_plan", "cancelled_node")
    return _log(state, "await_plan", "planning_agent")


def route_after_review(state: GraphState) -> str:
    approved = state.get("review_approved", False)
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", settings.review_max_iterations)
    if approved:
        return _log(state, "review_agent", "phase_gate")
    if iteration < max_iter:
        logger.info("[orchestrator] %s: review rejected (iter %d/%d)", state["issue"].key, iteration, max_iter)
        return _log(state, "review_agent", "coding_agent")
    return _log(state, "review_agent", "failed_node")


def route_after_phase_gate(state: GraphState) -> str:
    if state.get("status") == "coding":
        return _log(state, "phase_gate", "coding_agent")
    return _log(state, "phase_gate", "await_final")


def route_after_final_gate(state: GraphState) -> str:
    if state.get("status") == "creating_pr":
        return _log(state, "await_final", "pr_node")
    return _log(state, "await_final", "__end__")
