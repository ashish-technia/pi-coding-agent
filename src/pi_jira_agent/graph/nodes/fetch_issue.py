import logging

from ...jira_client import JiraClient
from .. import progress
from ..state import GraphState

logger = logging.getLogger(__name__)


def make_fetch_issue(jira: JiraClient):
    async def fetch_issue(state: GraphState) -> dict:
        key = state["issue_key"]
        progress.mark(key, "fetch_issue")

        # A run started with an inline issue (manual/dev entry) skips the Jira call.
        inline = state.get("issue")
        if inline is not None and inline.summary and not inline.url:
            logger.info("Using inline issue payload for %s", key)
            return {"issue": inline, "status": "framing_requirements", "current_node": "fetch_issue"}

        logger.info("Fetching Jira issue %s (summary, description, comments)", key)
        issue = await jira.get_issue(key)
        logger.info("Fetched %s: %d comment(s)", key, len(issue.comments))
        return {"issue": issue, "status": "framing_requirements", "current_node": "fetch_issue"}

    return fetch_issue
