"""Turning a run's repo selection into the things the nodes need.

A run stores only repo *names* in ``GraphState`` (checkpoints stay small and stable
across config edits). The registry of `RepoConfig` objects and the Bitbucket clients
keyed by name are built once in ``build.py`` and closed over by the nodes; the git
client for a run's worktree comes from ``workspace.RunWorkspaces``.
"""

from __future__ import annotations

from ..models import RepoConfig
from .state import GraphState

RepoMap = dict[str, RepoConfig]


def selected_repos(repo_map: RepoMap, state: GraphState) -> list[RepoConfig]:
    """The repos this run works in. The first is the primary (the Pi session's cwd).

    Falls back to the first configured repo when the state predates the selection
    field or names a repo that has since been removed from repos.json, so an
    in-flight run never dies because someone edited the config.
    """
    names = state.get("repos") or []
    chosen = [repo_map[name] for name in names if name in repo_map]
    if chosen:
        return chosen
    return list(repo_map.values())[:1]


def describe(repos: list[RepoConfig]) -> str:
    return ", ".join(r.name for r in repos) or "-"
