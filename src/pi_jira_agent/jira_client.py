import httpx


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_token)

    async def add_comment(self, issue_key: str, comment: str) -> None:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        payload = {"body": comment}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload, auth=self.auth)
            response.raise_for_status()

    async def transition_issue(self, issue_key: str, transition_id: str) -> None:
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        payload = {"transition": {"id": transition_id}}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload, auth=self.auth)
            response.raise_for_status()
