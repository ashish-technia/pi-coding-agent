import asyncio
import json
import os
import subprocess
from pathlib import Path

from .models import AgentResult, JiraIssue


class PiAgentExecutor:
    """
    Wrapper for Pi SDK usage.

    Replace `run` internals with actual Pi SDK calls in your environment.
    """

    def __init__(
        self,
        model: str,
        system_prompt: str,
        api_key: str,
        provider: str,
        node_command: str,
        runner_script: str,
        agent_dir: str,
        timeout_seconds: int,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.api_key = api_key
        self.provider = provider
        self.node_command = node_command
        self.runner_script = runner_script
        self.agent_dir = agent_dir
        self.timeout_seconds = timeout_seconds

    async def run(self, issue: JiraIssue) -> AgentResult:
        project_root = Path(__file__).resolve().parents[2]
        script_path = project_root / self.runner_script
        agent_dir_path = Path(self.agent_dir)
        if not agent_dir_path.is_absolute():
            agent_dir_path = project_root / agent_dir_path
        repo_cwd = str(project_root)
        execute_changes = False
        branch_name = None
        if getattr(self, "_execution_context", None):
            repo_cwd = self._execution_context.get("repo_cwd", repo_cwd)
            execute_changes = self._execution_context.get("execute_changes", False)
            branch_name = self._execution_context.get("branch_name")

        payload = {
            "issue": issue.model_dump(),
            "model": self.model,
            "systemPrompt": self.system_prompt,
            "provider": self.provider,
            "agentDir": str(agent_dir_path),
            "repoCwd": repo_cwd,
            "executeChanges": execute_changes,
            "branchName": branch_name,
        }

        env = os.environ.copy()
        env["PI_PROVIDER_API_KEY"] = self.api_key

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [self.node_command, str(script_path)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                cwd=repo_cwd,
                env=env,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Pi SDK runner command not found: {self.node_command}. "
                "Ensure Node.js is installed and PI_NODE_COMMAND is correct."
            ) from exc
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Pi SDK runner timed out after {self.timeout_seconds}s "
                f"(provider={self.provider}, model={self.model})"
            )

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            details = stderr or stdout or "<no stdout/stderr output>"
            raise RuntimeError(
                f"Pi SDK runner failed with exit code {completed.returncode} "
                f"(provider={self.provider}, model={self.model}, script={script_path}): "
                f"{details}"
            )

        raw = completed.stdout.strip()
        if not raw:
            raise RuntimeError("Pi SDK runner returned empty output.")

        return AgentResult.model_validate_json(raw)

    async def run_with_mode(
        self,
        issue: JiraIssue,
        *,
        repo_cwd: str,
        execute_changes: bool,
        branch_name: str | None = None,
    ) -> AgentResult:
        self._execution_context = {
            "repo_cwd": repo_cwd,
            "execute_changes": execute_changes,
            "branch_name": branch_name,
        }
        try:
            return await self.run(issue)
        finally:
            self._execution_context = None
