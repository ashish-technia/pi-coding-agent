import asyncio
import logging

from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, describe, selected_repos
from ..state import AsyncNode, GraphState

logger = logging.getLogger(__name__)

WorkspaceNode = AsyncNode


def make_prepare_workspace(workspaces: RunWorkspaces, repo_map: RepoMap) -> WorkspaceNode:
    async def prepare_workspace(state: GraphState) -> dict:
        """Give the run its own worktree in every selected repo, before anything reads the code.

        Planning and coding then see the same commit, and that commit is recorded so the
        PR step can tell what the run added.
        """
        key = state["issue_key"]
        progress.mark(key, "prepare_workspace")
        repos = selected_repos(repo_map, state)
        logger.info("Preparing worktrees for %s in repo(s) %s", key, describe(repos))

        base_shas: dict[str, str] = {}
        for repo in repos:
            if not repo.path.strip():
                continue
            base_shas[repo.name] = await asyncio.to_thread(workspaces.ensure, key, repo)

        return {"base_shas": base_shas, "status": "planning", "current_node": "prepare_workspace"}

    return prepare_workspace
