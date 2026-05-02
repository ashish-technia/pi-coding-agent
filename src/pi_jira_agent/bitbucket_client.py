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
            request_kwargs: dict = {"json": payload}
            if self.token:
                request_kwargs["headers"] = {"Authorization": f"Bearer {self.token}"}
            elif self.auth:
                request_kwargs["auth"] = self.auth
            else:
                raise ValueError("Bitbucket auth is not configured. Set token or username/password.")

            response = await client.post(url, **request_kwargs)
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
