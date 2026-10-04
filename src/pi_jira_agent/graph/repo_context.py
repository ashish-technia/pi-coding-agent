"""Turning a run's repo selection into the things the nodes need.

A run stores only repo *names* in ``GraphState`` (checkpoints stay small and stable
across config edits). The registry of `RepoConfig` objects, and the git/Bitbucket
clients keyed by name, are built once in ``build.py`` and closed over by the nodes.
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


def repo_roots_payload(repos: list[RepoConfig]) -> list[dict]:
    """The `repoRoots` block handed to the Pi runner.

    Pi resolves absolute paths as given, so listing the roots is all it takes to let
    one agent session read and edit several checkouts.
    """
    return [
        {"name": r.name, "path": r.path, "properties": r.properties}
        for r in repos
        if r.path.strip()
    ]


def describe(repos: list[RepoConfig]) -> str:
    return ", ".join(r.name for r in repos) or "-"
