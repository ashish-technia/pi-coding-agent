import asyncio
import logging

from ...bitbucket_client import BitbucketClient
from ...git_client import GitBranchClient
from ...jira_client import JiraClient
from ...models import AgentResult, JiraIssue, RepoConfig
from ...workspace import RunWorkspaces
from .. import progress
from ..repo_context import RepoMap, selected_repos
from ..state import AsyncNode, GraphState

logger = logging.getLogger(__name__)

PrNode = AsyncNode


def _description(code_result: AgentResult, repos: list[RepoConfig], issue_key: str) -> str:
    """The PR body, with a sibling note when the change spans several repositories.

    Bitbucket has no notion of a linked PR set, and the URLs of the other PRs do not
    exist yet while the first one is being opened, so the repo names are what each
    description can honestly carry. The Jira comment lists every URL afterwards.
    """
    if len(repos) < 2:
        return code_result.pr_description
    siblings = ", ".join(r.name for r in repos)
    return (
        f"{code_result.pr_description}\n\n"
        f"---\n"
        f"Part of a multi-repository change for {issue_key}, spanning: {siblings}. "
        f"These pull requests are meant to be reviewed and merged together."
    )


def _commit_and_push(git_branch: GitBranchClient, repo_name: str, branch: str, base: str, message: str) -> bool:
    """Commit what is uncommitted and push the branch. False when the repo has nothing to deliver."""
    if git_branch.has_changes():
        logger.info("Committing changes in repo %s on branch %s", repo_name, branch)
        git_branch.commit_all(message)
    elif git_branch.ahead_count(base) == 0:
        # Untouched repo: pushing would open an empty PR, so leave it alone.
        return False
    logger.info("Pushing branch %s in repo %s", branch, repo_name)
    git_branch.push_branch(branch)
    return True


def make_pr_node(
    bitbucket_clients: dict[str, BitbucketClient],
    workspaces: RunWorkspaces,
    repo_map: RepoMap,
) -> PrNode:
    async def pr_node(state: GraphState) -> dict:
        issue: JiraIssue = state["issue"]
        progress.mark(issue.key, "pr_node")
        code_result: AgentResult = state["code_result"]
        plan: AgentResult = state["plan_result"]
        branch = plan.branch_name or code_result.branch_name
        title = state.get("pr_title") or code_result.pr_title
        repos = selected_repos(repo_map, state)
        description = _description(code_result, repos, issue.key)

        pr_urls: dict[str, str] = {}
        skipped: list[str] = []
        base_shas = state.get("base_shas") or {}
        for repo in repos:
            bitbucket = bitbucket_clients.get(repo.name)
            if not repo.path.strip():
                raise RuntimeError(f"PR creation requires a local path for repo {repo.name!r}.")
            if not bitbucket:
                raise RuntimeError(f"PR creation requires a Bitbucket repo slug for repo {repo.name!r}.")
            git_branch: GitBranchClient = workspaces.git(state["issue_key"], repo.name)
            base = base_shas.get(repo.name) or f"{git_branch.remote_name}/{repo.target_branch}"

            # git runs as a blocking subprocess (a push can take minutes), so it stays off the event loop.
            delivered = await asyncio.to_thread(
                _commit_and_push, git_branch, repo.name, branch, base, code_result.commit_message
            )
            if not delivered:
                logger.info("No changes in repo %s for %s; skipping its PR.", repo.name, issue.key)
                skipped.append(repo.name)
                continue

            # Every step above is safe to repeat. This one is not, so a retry first looks for
            # the pull request an earlier attempt may already have opened.
            pr_result = await bitbucket.find_open_pull_request(
                source_branch=branch, destination_branch=repo.target_branch
            )
            if pr_result:
                logger.info("Reusing open PR in repo %s for %s: %s", repo.name, issue.key, pr_result.pr_url)
            else:
                logger.info("Creating Bitbucket PR in repo %s for issue %s: %s", repo.name, issue.key, title)
                pr_result = await bitbucket.create_pull_request(
                    title=title,
                    description=description,
                    source_branch=branch,
                    destination_branch=repo.target_branch,
                )
                logger.info("Bitbucket PR created in repo %s for %s: %s", repo.name, issue.key, pr_result.pr_url)
            pr_urls[repo.name] = pr_result.pr_url

        if skipped:
            logger.info("Repos with nothing to deliver for %s: %s", issue.key, ", ".join(skipped))

        # Still "creating_pr": the run is done once announce_node has told Jira.
        return {"status": "creating_pr", "pr_urls": pr_urls, "current_node": "pr_node"}

    return pr_node


def announcement(pr_urls: dict[str, str]) -> str:
    if len(pr_urls) == 1:
        (only,) = pr_urls.values()
        return f"Automation created PR: {only}"
    lines = "\n".join(f"- {name}: {url}" for name, url in pr_urls.items())
    return f"Automation created {len(pr_urls)} pull requests for this issue:\n{lines}"


def make_announce_node(
    jira: JiraClient,
    *,
    jira_transition_done_id: str | None,
    comments_enabled: bool = True,
) -> PrNode:
    async def announce_node(state: GraphState) -> dict:
        """Tell Jira about the pull requests, in a node of its own.

        `pr_urls` is checkpointed before this runs, so a failure here is retried without
        going near Bitbucket again.
        """
        key = state["issue"].key
        progress.mark(key, "announce_node")
        pr_urls = state.get("pr_urls") or {}

        if comments_enabled and pr_urls:
            body = announcement(pr_urls)
            # The transition below can fail after the comment was posted; a retry must not post it twice.
            posted = {comment.body.strip() for comment in (await jira.get_issue(key)).comments}
            if body.strip() in posted:
                logger.info("PR comment already on %s; not posting it again.", key)
            else:
                await jira.add_comment(key, body)
        if jira_transition_done_id and pr_urls:
            await jira.transition_issue(key, jira_transition_done_id)

        return {"status": "done", "current_node": "announce_node"}

    return announce_node
