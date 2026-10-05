import logging

import httpx

from .models import JiraIssue, issue_from_api

logger = logging.getLogger(__name__)

_ISSUE_FIELDS = "summary,description,comment,project,reporter,assignee,issuetype,status,labels"


def _jira_error_text(response: httpx.Response) -> str:
    """Jira's own explanation from the JSON body, else a trimmed body."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:300]
    if isinstance(body, dict):
        messages = list(body.get("errorMessages") or [])
        errors = body.get("errors") or {}
        if isinstance(errors, dict):
            messages += [f"{k}: {v}" for k, v in errors.items()]
        if messages:
            return "; ".join(str(m) for m in messages)
    return str(body)[:300]


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.auth = (email, api_token)

    async def get_issue(self, issue_key: str) -> JiraIssue:
        """Fetch summary, description and all comments for an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        logger.info("Jira GET %s?fields=%s (as %s)", url, _ISSUE_FIELDS, self.email)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params={"fields": _ISSUE_FIELDS}, auth=self.auth)
            if response.is_error:
                detail = _jira_error_text(response)
                logger.error(
                    "Jira GET %s failed: HTTP %s | %s | content-type=%s",
                    url,
                    response.status_code,
                    detail or "<empty body>",
                    response.headers.get("content-type", "?"),
                )
                if response.status_code == 404:
                    raise LookupError(
                        f"Jira issue {issue_key} not found or not visible to {self.email}. "
                        f"GET {url} -> 404: {detail}. "
                        "Check JIRA_BASE_URL (must be the site root, e.g. https://acme.atlassian.net), "
                        "the issue key, and that the account can browse this project."
                    )
                if response.status_code in (401, 403):
                    raise RuntimeError(
                        f"Jira rejected the credentials for {self.email}: GET {url} -> "
                        f"{response.status_code}: {detail}. Check JIRA_EMAIL / JIRA_API_TOKEN."
                    )
                raise RuntimeError(f"Jira GET {url} failed with HTTP {response.status_code}: {detail}")
            payload = response.json()
        issue = issue_from_api(payload, base_url=self.base_url)
        logger.info(
            "Jira issue %s fetched: type=%s status=%s comments=%d",
            issue.key,
            issue.issue_type,
            issue.status,
            len(issue.comments),
        )
        return issue

    async def add_comment(self, issue_key: str, comment: str) -> str:
        """Post a plain-text comment (rendered as ADF paragraphs). Returns the comment id."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        payload = {"body": _text_to_adf(comment)}
        logger.info("Jira POST %s (%d chars)", url, len(comment))
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload, auth=self.auth)
            if response.is_error:
                detail = _jira_error_text(response)
                logger.error("Jira POST %s failed: HTTP %s | %s", url, response.status_code, detail)
                raise RuntimeError(f"Jira comment on {issue_key} failed: HTTP {response.status_code}: {detail}")
            return str(response.json().get("id", ""))

    async def transition_issue(self, issue_key: str, transition_id: str) -> None:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        payload = {"transition": {"id": transition_id}}
        logger.info("Jira POST %s transition=%s", url, transition_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload, auth=self.auth)
            if response.is_error:
                detail = _jira_error_text(response)
                logger.error("Jira POST %s failed: HTTP %s | %s", url, response.status_code, detail)
                raise RuntimeError(
                    f"Jira transition {transition_id} on {issue_key} failed: HTTP {response.status_code}: {detail}"
                )


def _text_to_adf(text: str) -> dict:
    """Minimal Atlassian Document Format: one paragraph per line, blank lines preserved."""
    paragraphs = []
    for line in text.splitlines() or [""]:
        content = [{"type": "text", "text": line}] if line else []
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}
