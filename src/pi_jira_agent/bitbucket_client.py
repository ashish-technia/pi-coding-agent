import httpx

from .models import PullRequestResult


class BitbucketClient:
    def __init__(
        self,
        base_url: str,
        workspace: str,
        repo_slug: str,
        username: str,
        app_password: str,
        token: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace
        self.repo_slug = repo_slug
        self.auth = (username, app_password) if username and app_password else None
        self.token = token

    def _request_kwargs(self) -> dict:
        if self.token:
            return {"headers": {"Authorization": f"Bearer {self.token}"}}
        if self.auth:
            return {"auth": self.auth}
        raise ValueError("Bitbucket auth is not configured. Set token or username/password.")

    async def find_open_pull_request(self, *, source_branch: str, destination_branch: str) -> PullRequestResult | None:
        """The open pull request from ``source_branch`` into ``destination_branch``, if there is one.

        Asked before creating, so a retried PR step reuses what an earlier attempt opened.
        """
        url = f"{self.base_url}/2.0/repositories/{self.workspace}/{self.repo_slug}/pullrequests"
        query = (
            f'source.branch.name = "{source_branch}" AND destination.branch.name = "{destination_branch}" '
            'AND state = "OPEN"'
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params={"q": query, "pagelen": 1}, **self._request_kwargs())
            if response.is_error:
                raise RuntimeError(
                    f"Bitbucket PR lookup failed status={response.status_code} "
                    f"url={url} source={source_branch} destination={destination_branch} "
                    f"response={response.text}"
                )
            values = response.json().get("values") or []
        if not values:
            return None
        return PullRequestResult(pr_id=values[0]["id"], pr_url=values[0]["links"]["html"]["href"])

    async def create_pull_request(
        self,
        *,
        title: str,
        description: str,
        source_branch: str,
        destination_branch: str,
    ) -> PullRequestResult:
        url = f"{self.base_url}/2.0/repositories/{self.workspace}/{self.repo_slug}/pullrequests"
        payload = {
            "title": title,
            "description": description,
            "source": {"branch": {"name": source_branch}},
            "destination": {"branch": {"name": destination_branch}},
            "close_source_branch": False,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, **self._request_kwargs())
            if response.is_error:
                details = response.text
                raise RuntimeError(
                    f"Bitbucket PR create failed status={response.status_code} "
                    f"url={url} source={source_branch} destination={destination_branch} "
                    f"response={details}"
                )
            body = response.json()

        return PullRequestResult(
            pr_id=body["id"],
            pr_url=body["links"]["html"]["href"],
        )
