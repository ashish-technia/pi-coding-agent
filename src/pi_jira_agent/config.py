from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "pi-jira-agent"
    webhook_secret: str = Field(..., description="Shared secret for Jira webhook auth.")
    allowed_projects: str = Field(
        default="",
        description="Comma-separated Jira project keys allowed for processing.",
    )
    use_queue: bool = True
    queue_max_size: int = 100

    pi_api_key: str = Field(..., description="API key for Pi provider if required.")
    pi_provider: str = Field(
        default="openai",
        description="Provider key name for Pi model registry auth storage.",
    )
    pi_model: str = Field(default="gpt-5.4-medium", description="Model used by Pi agent.")
    pi_system_prompt: str = (
        "You are an engineering automation agent. Prepare branch updates and PR content."
    )
    pi_node_command: str = Field(default="node", description="Node.js executable command.")
    pi_runner_script: str = Field(
        default="node/pi-sdk-runner.mjs",
        description="Relative path to Pi SDK runner script.",
    )
    pi_agent_dir: str = Field(
        default=".pi-agent",
        description="Relative path to Pi AGENTS/skills/prompts directory.",
    )
    pi_timeout_seconds: int = Field(
        default=120,
        description="Timeout in seconds for Pi SDK runner execution.",
    )
    pi_execute_changes: bool = Field(
        default=False,
        description="Run Pi in repository execution mode to apply code changes.",
    )
    create_pr: bool = Field(
        default=False,
        description="Create Bitbucket PR when true; when false only logs proposed changes.",
    )
    prepare_branch_before_pr: bool = Field(
        default=True,
        description="Create/switch and push source branch in local repo before PR creation.",
    )
    repo_local_path: str = Field(
        default="",
        description="Local repository path used for branch preparation.",
    )
    git_remote_name: str = Field(default="origin", description="Git remote name for push/fetch.")

    bitbucket_base_url: str = Field(..., description="Bitbucket API base URL.")
    bitbucket_workspace: str = Field(..., description="Bitbucket workspace key.")
    bitbucket_repo_slug: str = Field(..., description="Bitbucket repository slug.")
    bitbucket_username: str = Field(
        default="",
        description="Bitbucket API user for basic auth mode.",
    )
    bitbucket_app_password: str = Field(
        default="",
        description="Bitbucket app password for basic auth mode.",
    )
    bitbucket_token: str = Field(
        default="",
        description="Bitbucket bearer token. If set, bearer auth is used.",
    )
    bitbucket_target_branch: str = "develop"

    jira_base_url: str = Field(..., description="Jira instance base URL.")
    jira_email: str = Field(..., description="Jira account email.")
    jira_api_token: str = Field(..., description="Jira API token.")
    jira_transition_done_id: str | None = Field(
        default=None,
        description="Optional transition id to mark ticket as done/in review.",
    )

    def allowed_projects_set(self) -> set[str]:
        if not self.allowed_projects.strip():
            return set()
        return {item.strip().upper() for item in self.allowed_projects.split(",") if item.strip()}


settings = Settings()
