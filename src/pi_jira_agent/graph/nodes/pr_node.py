import logging
from collections.abc import Awaitable, Callable

from ...bitbucket_client import BitbucketClient
from ...git_client import GitBranchClient
from ...jira_client import JiraClient
from ...models import AgentResult, JiraIssue, RepoConfig
from .. import progress
from ..repo_context import RepoMap, selected_repos
from ..state import GraphState

logger = logging.getLogger(__name__)

PrNode = Callable[[GraphState], Awaitable[dict]]


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


def make_pr_node(
    bitbucket_clients: dict[str, BitbucketClient],
    jira: JiraClient,
    git_clients: dict[str, GitBranchClient],
    repo_map: RepoMap,
    *,
    jira_transition_done_id: str | None,
    comments_enabled: bool = True,
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
        for repo in repos:
            git_branch = git_clients.get(repo.name)
            bitbucket = bitbucket_clients.get(repo.name)
            if not git_branch:
                raise RuntimeError(f"PR creation requires a local path for repo {repo.name!r}.")
            if not bitbucket:
                raise RuntimeError(f"PR creation requires a Bitbucket repo slug for repo {repo.name!r}.")

            if git_branch.has_changes():
                logger.info("Committing changes in repo %s on branch %s", repo.name, branch)
                git_branch.commit_all(code_result.commit_message)
            elif git_branch.ahead_count(target_branch=repo.target_branch, source_branch=branch) == 0:
                # Untouched repo: pushing would open an empty PR, so leave it alone.
                logger.info("No changes in repo %s for %s; skipping its PR.", repo.name, issue.key)
                skipped.append(repo.name)
                continue

            logger.info("Pushing branch %s in repo %s", branch, repo.name)
            git_branch.push_branch(branch)

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

        if comments_enabled and pr_urls:
            if len(pr_urls) == 1:
                (only,) = pr_urls.values()
                body = f"Automation created PR: {only}"
            else:
                lines = "\n".join(f"- {name}: {url}" for name, url in pr_urls.items())
                body = f"Automation created {len(pr_urls)} pull requests for this issue:\n{lines}"
            await jira.add_comment(issue.key, body)
        if jira_transition_done_id and pr_urls:
            await jira.transition_issue(issue.key, jira_transition_done_id)

        return {"status": "done", "pr_urls": pr_urls, "current_node": "pr_node"}

    return pr_node
