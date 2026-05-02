import logging

from .bitbucket_client import BitbucketClient
from .config import settings
from .git_client import GitBranchClient
from .jira_client import JiraClient
from .models import JiraIssue
from .pi_agent import PiAgentExecutor

logger = logging.getLogger(__name__)


class AutomationService:
    def __init__(self):
        self.pi_agent = PiAgentExecutor(
            model=settings.pi_model,
            system_prompt=settings.pi_system_prompt,
            api_key=settings.pi_api_key,
            provider=settings.pi_provider,
            node_command=settings.pi_node_command,
            runner_script=settings.pi_runner_script,
            agent_dir=settings.pi_agent_dir,
            timeout_seconds=settings.pi_timeout_seconds,
        )
        self.bitbucket = BitbucketClient(
            base_url=settings.bitbucket_base_url,
            workspace=settings.bitbucket_workspace,
            repo_slug=settings.bitbucket_repo_slug,
            username=settings.bitbucket_username,
            app_password=settings.bitbucket_app_password,
            token=settings.bitbucket_token,
        )
        self.jira = JiraClient(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
        )
        self.git_branch = None
        if settings.repo_local_path.strip():
            self.git_branch = GitBranchClient(
                repo_path=settings.repo_local_path,
                remote_name=settings.git_remote_name,
            )

    async def process_issue(self, issue: JiraIssue) -> None:
        logger.info("Processing issue %s", issue.key)
        logger.info("Running Pi generation for issue %s", issue.key)
        agent_result = await self.pi_agent.run_with_mode(
            issue,
            repo_cwd=settings.repo_local_path or ".",
            execute_changes=False,
        )
        logger.info("Pi generation completed for issue %s", issue.key)
        logger.info(
            "Generated change log for %s | branch=%s | commit=%s | title=%s | files=%s | description=%s",
            issue.key,
            agent_result.branch_name,
            agent_result.commit_message,
            agent_result.pr_title,
            ",".join(agent_result.files_changed) if agent_result.files_changed else "<none>",
            agent_result.pr_description.replace("\n", " | "),
        )

        if not settings.create_pr:
            logger.info(
                "CREATE_PR is disabled; skipping Bitbucket PR creation for issue %s.",
                issue.key,
            )
            return

        if settings.prepare_branch_before_pr:
            if not self.git_branch:
                raise RuntimeError(
                    "Branch preparation is enabled but REPO_LOCAL_PATH is not configured."
                )
            logger.info(
                "Preparing source branch %s from %s in repo %s",
                agent_result.branch_name,
                settings.bitbucket_target_branch,
                settings.repo_local_path,
            )
            self.git_branch.prepare_branch(
                target_branch=settings.bitbucket_target_branch,
                source_branch=agent_result.branch_name,
                push=not settings.pi_execute_changes,
            )

        if settings.pi_execute_changes:
            if not self.git_branch:
                raise RuntimeError("PI_EXECUTE_CHANGES requires REPO_LOCAL_PATH to be configured.")
            logger.info("Running Pi repository execution for issue %s", issue.key)
            agent_result = await self.pi_agent.run_with_mode(
                issue,
                repo_cwd=settings.repo_local_path,
                execute_changes=True,
                branch_name=agent_result.branch_name,
            )
            logger.info("Pi repository execution completed for issue %s", issue.key)
            if not self.git_branch.has_changes():
                logger.info("Skipping PR creation for issue %s: no code changes produced.", issue.key)
                return
            logger.info("Committing and pushing changes for branch %s", agent_result.branch_name)
            self.git_branch.commit_all(agent_result.commit_message)
            self.git_branch.push_branch(agent_result.branch_name)

        if settings.prepare_branch_before_pr and self.git_branch:
            ahead = self.git_branch.ahead_count(
                target_branch=settings.bitbucket_target_branch,
                source_branch=agent_result.branch_name,
            )
            if ahead == 0:
                logger.info(
                    "Skipping PR creation for issue %s: source branch %s has no commits ahead of %s.",
                    issue.key,
                    agent_result.branch_name,
                    settings.bitbucket_target_branch,
                )
                return

        logger.info("Creating Bitbucket PR for issue %s", issue.key)
        pr_result = await self.bitbucket.create_pull_request(
            title=agent_result.pr_title,
            description=agent_result.pr_description,
            source_branch=agent_result.branch_name,
            destination_branch=settings.bitbucket_target_branch,
        )
        logger.info("Bitbucket PR created for issue %s: %s", issue.key, pr_result.pr_url)

        await self.jira.add_comment(
            issue.key,
            f"Automation created PR: {pr_result.pr_url}",
        )
        if settings.jira_transition_done_id:
            await self.jira.transition_issue(issue.key, settings.jira_transition_done_id)

        logger.info("Issue %s done. PR=%s", issue.key, pr_result.pr_url)
