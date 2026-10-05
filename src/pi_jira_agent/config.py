import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import RepoConfig

Stage = Literal["requirements", "planning", "coding", "review"]


class StageModelConfig(BaseModel):
    """Provider, model and key used by one stage of the workflow."""

    provider: str
    model: str
    api_key: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "pi-jira-agent"
    webhook_secret: str = Field(..., description="Shared secret for Jira webhook / automation calls.")
    allowed_projects: str = Field(
        default="",
        description="Comma-separated Jira project keys allowed for processing.",
    )
    use_queue: bool = True
    queue_max_size: int = 100

    # --- Persistence -------------------------------------------------------
    database_url: str = Field(
        default="",
        description=(
            "Postgres DSN for the LangGraph checkpointer, e.g. "
            "postgresql://pijira:pijira@localhost:5440/pijira. Empty = SQLite file."
        ),
    )
    graph_checkpoint_db: str = Field(
        default="data/graph_checkpoints.sqlite",
        description="SQLite checkpoint path used when DATABASE_URL is empty.",
    )
    redis_url: str = Field(
        default="",
        description="Redis URL for the job queue, e.g. redis://localhost:6390/0. Empty = in-memory queue.",
    )

    # --- Pi coding harness (planning + coding stages) ----------------------
    pi_api_key: str = Field(default="", description="Default API key for the Pi provider.")
    pi_provider: str = Field(default="openai", description="Default Pi provider (openai, anthropic, google, ...).")
    pi_model: str = Field(default="gpt-5.4-medium", description="Default Pi model id.")
    pi_system_prompt: str = "You are an engineering automation agent. Prepare branch updates and PR content."
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
        default=600,
        description=(
            "Timeout in seconds for one Pi SDK runner execution. Plan mode explores the "
            "repository and execute mode edits it, so both take minutes on real repos; "
            "large monorepos with a high thinking level can need 10 minutes or more."
        ),
    )
    max_concurrent_runs: int = Field(
        default=2,
        ge=1,
        description=(
            "How many Pi sessions (planning or coding) may run at once across all runs. "
            "Runs waiting at a gate do not count; extra runs wait for a free slot."
        ),
    )
    pi_thinking_level: str = Field(
        default="medium",
        description=(
            "Pi reasoning effort for both agents: off, minimal, low, medium, high, xhigh. "
            "Clamped to 'off' automatically for models without reasoning support."
        ),
    )

    # --- Per-stage model overrides -----------------------------------------
    # Planning and coding run inside Pi; requirements and review are direct chat calls.
    # Every override falls back to the Pi defaults (planning/coding) or the review
    # settings (requirements) when left empty.
    planning_model_provider: str = ""
    planning_model: str = ""
    planning_api_key: str = ""
    coding_model_provider: str = ""
    coding_model: str = ""
    coding_api_key: str = ""
    requirements_model_provider: str = ""
    requirements_model: str = ""
    requirements_api_key: str = ""
    review_model_provider: str = Field(
        default="anthropic",
        description="LLM provider for the review agent: 'anthropic', 'openai' or 'google'.",
    )
    review_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model id used by the review agent. Must match REVIEW_MODEL_PROVIDER.",
    )
    review_api_key: str = Field(
        default="",
        description=(
            "API key for the review agent's LLM. Falls back to the provider's standard "
            "env var (ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY) if unset."
        ),
    )
    review_max_iterations: int = Field(
        default=2,
        description="Max coding/review retry loops per phase before giving up.",
    )
    review_max_diff_chars: int = Field(
        default=200_000,
        ge=0,
        description=(
            "Largest diff, in characters, sent to the review model. Above it whole files are "
            "left out (lockfiles and generated files first) and named in the prompt and at "
            "the gates. 0 disables the cap."
        ),
    )
    review_rules_path: str = Field(
        default="review-rules.md",
        description="Markdown file with the team's must-check review rules, fed to the review agent.",
    )

    # --- Repository & delivery ---------------------------------------------
    repo_local_path: str = Field(
        default="",
        description="Local clone of the target repository the agents work in (single-repo setups).",
    )
    repos_config_path: str = Field(
        default="repos.json",
        description=(
            "JSON file listing every repository the agents may work in. When the file is "
            "missing, REPO_LOCAL_PATH is used as the one and only repository."
        ),
    )
    repos_root: str = Field(
        default="",
        description=(
            "When set, every repo's clone lives at <REPOS_ROOT>/<name> and the `path` in "
            "repos.json is ignored. Docker sets it to /workspace so one repos.json works "
            "on the host (its own paths) and in the container (cloned under the root)."
        ),
    )
    bitbucket_host: str = Field(
        default="bitbucket.org",
        description="Git host used to derive clone URLs when a repo has no explicit clone_url.",
    )
    git_remote_name: str = Field(default="origin", description="Git remote name for push/fetch.")
    runs_root: str = Field(
        default="data/runs",
        description=(
            "Where each run's git worktrees live, as <RUNS_ROOT>/<issue key>/<repo name>. "
            "They hold uncommitted work while a run waits at a gate, so this must survive "
            "restarts (a volume in Docker)."
        ),
    )
    pr_creation_enabled: bool = Field(
        default=False,
        description="Show the 'Create PR' action and allow commit/push/PR. False = review only.",
    )
    create_pr: bool | None = Field(
        default=None,
        description="Deprecated alias of PR_CREATION_ENABLED; kept for existing .env files.",
    )

    bitbucket_base_url: str = Field(..., description="Bitbucket API base URL.")
    bitbucket_workspace: str = Field(..., description="Bitbucket workspace key.")
    bitbucket_repo_slug: str = Field(..., description="Bitbucket repository slug.")
    bitbucket_username: str = Field(default="", description="Bitbucket API user for basic auth mode.")
    bitbucket_app_password: str = Field(default="", description="Bitbucket app password for basic auth mode.")
    bitbucket_token: str = Field(default="", description="Bitbucket bearer token. If set, bearer auth is used.")
    bitbucket_target_branch: str = "develop"

    # --- Jira --------------------------------------------------------------
    jira_base_url: str = Field(..., description="Jira instance base URL.")
    jira_email: str = Field(..., description="Jira account email (the agent's service account in flow 2).")
    jira_api_token: str = Field(..., description="Jira API token.")
    jira_transition_done_id: str | None = Field(
        default=None,
        description="Optional transition id to apply after the PR is created.",
    )
    jira_comments_enabled: bool = Field(
        default=True,
        description="When false, the workflow never posts comments back to Jira.",
    )
    # Flow 2: Jira-comment channel. Off by default; flow 1 runs entirely in the UI.
    jira_comment_channel_enabled: bool = Field(
        default=False,
        description="Post every feedback request as a Jira comment and accept /commands in replies.",
    )
    jira_agent_account_id: str = Field(
        default="",
        description="Atlassian accountId of the agent's service account (ignore its own comments).",
    )
    jira_trigger_label: str = Field(
        default="ai-agent",
        description="Label that marks an issue for the agent (used by the Jira Automation rule).",
    )

    # ----------------------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                "DATABASE_URL must be a Postgres DSN such as "
                "postgresql://pijira:pijira@localhost:5440/pijira (or empty for SQLite); "
                f"got {value!r}"
            )
        return value

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError(
                "REDIS_URL must start with redis:// (e.g. redis://localhost:6390/0) "
                f"or be empty for the in-memory queue; got {value!r}"
            )
        return value

    @field_validator("jira_base_url")
    @classmethod
    def _check_jira_base_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"JIRA_BASE_URL must be an absolute URL like https://acme.atlassian.net; got {value!r}")
        if parsed.path and parsed.path != "/":
            raise ValueError(
                "JIRA_BASE_URL must be the site root with no path (the API lives under /rest/api/3). "
                f"Remove {parsed.path!r}, e.g. use {parsed.scheme}://{parsed.netloc}"
            )
        return value

    @field_validator("pi_thinking_level")
    @classmethod
    def _check_thinking_level(cls, value: str) -> str:
        allowed = {"off", "minimal", "low", "medium", "high", "xhigh"}
        if value not in allowed:
            raise ValueError(f"PI_THINKING_LEVEL must be one of {sorted(allowed)}; got {value!r}")
        return value

    def allowed_projects_set(self) -> set[str]:
        if not self.allowed_projects.strip():
            return set()
        return {item.strip().upper() for item in self.allowed_projects.split(",") if item.strip()}

    @property
    def pr_enabled(self) -> bool:
        if self.create_pr is not None:
            return self.create_pr
        return self.pr_creation_enabled

    def stage_model(self, stage: Stage) -> StageModelConfig:
        """Resolve provider/model/key for a stage with the documented fallbacks."""
        if stage == "planning":
            return StageModelConfig(
                provider=self.planning_model_provider or self.pi_provider,
                model=self.planning_model or self.pi_model,
                api_key=self.planning_api_key or self.pi_api_key,
            )
        if stage == "coding":
            return StageModelConfig(
                provider=self.coding_model_provider or self.pi_provider,
                model=self.coding_model or self.pi_model,
                api_key=self.coding_api_key or self.pi_api_key,
            )
        if stage == "requirements":
            return StageModelConfig(
                provider=self.requirements_model_provider or self.review_model_provider,
                model=self.requirements_model or self.review_model,
                api_key=self.requirements_api_key or self.review_api_key,
            )
        return StageModelConfig(
            provider=self.review_model_provider,
            model=self.review_model,
            api_key=self.review_api_key,
        )

    def review_rules(self) -> str:
        path = Path(self.review_rules_path)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def default_clone_url(self, repo_slug: str) -> str:
        """`https://<host>/<workspace>/<slug>.git`, or "" when anything is missing.

        No credentials are embedded: the entrypoint configures a git credential helper
        from the same Bitbucket token/app password used for pushing.
        """
        if not (repo_slug.strip() and self.bitbucket_workspace.strip() and self.bitbucket_host.strip()):
            return ""
        return f"https://{self.bitbucket_host.strip('/')}/{self.bitbucket_workspace}/{repo_slug}.git"

    def repos(self) -> list[RepoConfig]:
        """Every repository the agents may work in, in the order repos.json lists them.

        That order is what makes the first selected repo a stable "primary" (the Pi
        session's cwd and the branch the verification commands run in), independent of
        the order the user ticked the boxes in.
        """
        path = Path(self.repos_config_path)
        entries: list[dict] = []
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("repos", []) if isinstance(raw, dict) else list(raw)

        if not entries:
            # No repos.json: the single REPO_LOCAL_PATH clone is the whole world.
            return [
                RepoConfig(
                    name=self.bitbucket_repo_slug or "repo",
                    path=self.repo_local_path,
                    clone_url=self.default_clone_url(self.bitbucket_repo_slug),
                    bitbucket_repo_slug=self.bitbucket_repo_slug,
                    target_branch=self.bitbucket_target_branch,
                    default_selected=True,
                )
            ]

        repos: list[RepoConfig] = []
        seen: set[str] = set()
        for entry in entries:
            repo = RepoConfig.model_validate(entry)
            if not repo.name:
                raise ValueError(f"Every repo in {self.repos_config_path} needs a name.")
            if repo.name in seen:
                raise ValueError(f"Duplicate repo name {repo.name!r} in {self.repos_config_path}.")
            seen.add(repo.name)
            # In a container the host paths in repos.json are meaningless; every clone
            # lives under REPOS_ROOT instead, named after the repo.
            if self.repos_root.strip():
                repo.path = str(Path(self.repos_root) / repo.name)
            # Absolute paths matter: the Pi runner addresses secondary repos by absolute
            # path, and only the primary one is ever the process cwd.
            if repo.path.strip():
                repo.path = str(Path(repo.path).expanduser().resolve())
            repo.clone_url = repo.clone_url.strip() or self.default_clone_url(
                repo.bitbucket_repo_slug or self.bitbucket_repo_slug
            )
            repo.bitbucket_repo_slug = repo.bitbucket_repo_slug or self.bitbucket_repo_slug
            repo.target_branch = repo.target_branch or self.bitbucket_target_branch
            repos.append(repo)
        return repos

    def selected_repos(self, names: Sequence[str] | None = None) -> list[RepoConfig]:
        """Resolve the repos a run works in, keeping repos.json order.

        ``None`` or an empty selection means the repos flagged ``default_selected``,
        falling back to the first one so a run always has a primary repository.
        """
        available = self.repos()
        if not names:
            return [r for r in available if r.default_selected] or available[:1]
        wanted = set(names)
        unknown = sorted(wanted - {r.name for r in available})
        if unknown:
            known = ", ".join(r.name for r in available)
            raise ValueError(f"Unknown repo(s) {', '.join(unknown)}. Configured repos: {known}.")
        return [r for r in available if r.name in wanted]

    def public_view(self) -> dict:
        """Non-secret configuration for the settings screen."""
        stages = {
            s: self.stage_model(s).model_dump(exclude={"api_key"})
            for s in ("requirements", "planning", "coding", "review")
        }
        return {
            "app_name": self.app_name,
            "stages": stages,
            "pi": {
                "thinking_level": self.pi_thinking_level,
                "timeout_seconds": self.pi_timeout_seconds,
                "max_concurrent_runs": self.max_concurrent_runs,
                "agent_dir": self.pi_agent_dir,
            },
            "repo": {
                "local_path": self.repo_local_path,
                "remote": self.git_remote_name,
                "target_branch": self.bitbucket_target_branch,
                "runs_root": self.runs_root,
                "config_path": self.repos_config_path,
            },
            # The picker the UI renders as checkboxes on the start form.
            "repos": [r.model_dump() for r in self.repos()],
            "delivery": {
                "pr_creation_enabled": self.pr_enabled,
                "bitbucket_workspace": self.bitbucket_workspace,
                "bitbucket_repo_slug": self.bitbucket_repo_slug,
                "jira_base_url": self.jira_base_url,
                "jira_comments_enabled": self.jira_comments_enabled,
                "jira_comment_channel_enabled": self.jira_comment_channel_enabled,
                "jira_trigger_label": self.jira_trigger_label,
            },
            "review": {
                "max_iterations": self.review_max_iterations,
                "max_diff_chars": self.review_max_diff_chars,
                "rules_path": self.review_rules_path,
                "rules": self.review_rules(),
            },
            "persistence": {
                "checkpointer": "postgres" if self.database_url else "sqlite",
                "queue": "redis" if self.redis_url else "memory",
            },
        }


settings = Settings()  # pyright: ignore[reportCallIssue]  # required values come from the environment
