import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .graph import progress
from .models import AgentResult, JiraIssue, RequirementsSpec

logger = logging.getLogger(__name__)

_EVENT_PREFIX = "@@PI "


# What the agent's process may see of the service's environment. The service holds the Jira,
# Bitbucket, database and webhook secrets in its own environment; the agent's bash can print
# whatever its process inherits, so it inherits only what a shell needs to work.
_BASE_ENV_POSIX = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TERM")
_BASE_ENV_WINDOWS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
)


def pi_environment(api_key: str, passthrough: list[str]) -> dict[str, str]:
    """The environment the Pi runner starts with: a fixed base, the named extras, the model key.

    This covers the environment only. Files the service can read (its `.env`, git
    credentials) are still readable by the agent until each run has its own sandbox.
    """
    base = _BASE_ENV_WINDOWS if sys.platform == "win32" else _BASE_ENV_POSIX
    env = {name: os.environ[name] for name in (*base, *passthrough) if name in os.environ}
    env["PI_PROVIDER_API_KEY"] = api_key
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the runner and everything it started: node, the agent's bash, and what bash ran."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)  # the runner leads its own process group
    except OSError:
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_streaming(
    cmd: list[str],
    *,
    stdin_text: str,
    cwd: str,
    env: dict,
    timeout: int,
    on_event,
    stop: threading.Event | None = None,
) -> tuple[int, str, str]:
    """Run the Node runner, forwarding ``@@PI`` JSON lines from stderr as they arrive.

    Returns (returncode, stdout, other_stderr). On timeout, or when ``stop`` is set, kills
    the whole process tree: the agent's bash may have started builds or servers of its own.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        # Its own process group (session on POSIX), so the tree can be killed as one.
        start_new_session=sys.platform != "win32",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0,
    )
    stdout_chunks: list[str] = []
    stderr_lines: list[str] = []

    def pump_stdout() -> None:
        assert proc.stdout is not None
        stdout_chunks.append(proc.stdout.read())

    def pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.startswith(_EVENT_PREFIX):
                try:
                    on_event(json.loads(line[len(_EVENT_PREFIX) :]))
                except (ValueError, TypeError):
                    stderr_lines.append(line.rstrip())
            else:
                stderr_lines.append(line.rstrip())

    threads = [threading.Thread(target=pump_stdout, daemon=True), threading.Thread(target=pump_stderr, daemon=True)]
    for t in threads:
        t.start()
    assert proc.stdin is not None
    proc.stdin.write(stdin_text)
    proc.stdin.close()

    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        stopped = stop is not None and stop.is_set()
        if stopped or time.monotonic() > deadline:
            _kill_tree(proc)
            for t in threads:
                t.join(timeout=5)
            if stopped:
                raise RuntimeError("Pi SDK runner was stopped because its run was cancelled.")
            raise subprocess.TimeoutExpired(cmd, timeout)
        time.sleep(0.25)
    for t in threads:
        t.join(timeout=10)
    return proc.returncode, "".join(stdout_chunks), "\n".join(stderr_lines)


class PiAgentExecutor:
    """
    Bridge to the Pi coding harness.

    One instance per stage (planning, coding) so each can use its own provider/model.
    Spawns ``node/pi-sdk-runner.mjs`` with a JSON payload on stdin and parses the
    AgentResult JSON it prints on stdout.
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
        thinking_level: str = "medium",
        env_passthrough: list[str] | None = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.api_key = api_key
        self.provider = provider
        self.node_command = node_command
        self.runner_script = runner_script
        self.agent_dir = agent_dir
        self.timeout_seconds = timeout_seconds
        self.thinking_level = thinking_level
        self.env_passthrough = env_passthrough or []
        self._execution_context: dict | None = None

    async def run(self, issue: JiraIssue) -> AgentResult:
        project_root = Path(__file__).resolve().parents[2]
        script_path = project_root / self.runner_script
        agent_dir_path = Path(self.agent_dir)
        if not agent_dir_path.is_absolute():
            agent_dir_path = project_root / agent_dir_path
        ctx = self._execution_context or {}
        repo_cwd = ctx.get("repo_cwd") or str(project_root)
        plan: AgentResult | None = ctx.get("plan")
        requirements: RequirementsSpec | None = ctx.get("requirements")

        payload = {
            "issue": issue.model_dump(),
            "model": self.model,
            "systemPrompt": self.system_prompt,
            "provider": self.provider,
            "agentDir": str(agent_dir_path),
            "repoCwd": repo_cwd,
            # Every repository this run may touch. The runner addresses the non-primary
            # ones by absolute path; an empty list means "just repoCwd".
            "repoRoots": ctx.get("repo_roots") or [],
            "executeChanges": ctx.get("execute_changes", False),
            "branchName": ctx.get("branch_name"),
            "thinkingLevel": self.thinking_level,
            # Approved requirements (both modes): the contract the change must satisfy.
            "requirements": requirements.model_dump() if requirements else None,
            # Execute mode: the approved plan (or the current phase of it) is the work order.
            # Plan mode: the previous plan being refined (None on a first plan).
            "plan": plan.model_dump() if plan else None,
            # Plan mode only: the human reviewer's refinement notes.
            "reviewerNotes": ctx.get("reviewer_notes") or "",
            # Execute mode only: feedback from the last rejected review.
            "reviewFeedback": ctx.get("review_feedback") or "",
        }

        env = pi_environment(self.api_key, self.env_passthrough)
        # Set when this coroutine is cancelled (shutdown): the worker thread cannot be
        # cancelled, so it is told to kill the process tree and return.
        stop = threading.Event()

        def on_event(event: dict) -> None:
            progress.add_event(issue.key, {"source": "pi", **event})
            ev = event.get("ev")
            if ev == "tool":
                logger.info("[pi %s] %s %s", issue.key, event.get("tool"), event.get("args", ""))
            elif ev in {"start", "done", "validation"}:
                logger.info("[pi %s] %s %s", issue.key, ev, {k: v for k, v in event.items() if k not in {"ev", "t"}})

        try:
            returncode, stdout, stderr = await asyncio.to_thread(
                _run_streaming,
                [self.node_command, str(script_path)],
                stdin_text=json.dumps(payload),
                cwd=repo_cwd,
                env=env,
                timeout=self.timeout_seconds,
                on_event=on_event,
                stop=stop,
            )
        except asyncio.CancelledError:
            stop.set()
            raise
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Pi SDK runner command not found: {self.node_command}. "
                "Ensure Node.js is installed and PI_NODE_COMMAND is correct."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            progress.add_event(
                issue.key, {"source": "pi", "ev": "error", "text": f"timed out after {self.timeout_seconds}s"}
            )
            raise RuntimeError(
                f"Pi SDK runner timed out after {self.timeout_seconds}s "
                f"(provider={self.provider}, model={self.model}). Raise PI_TIMEOUT_SECONDS or lower PI_THINKING_LEVEL."
            ) from exc

        if returncode != 0:
            details = stderr.strip() or stdout.strip() or "<no stdout/stderr output>"
            progress.add_event(issue.key, {"source": "pi", "ev": "error", "text": details[-400:]})
            raise RuntimeError(
                f"Pi SDK runner failed with exit code {returncode} "
                f"(provider={self.provider}, model={self.model}, script={script_path}): "
                f"{details}"
            )

        raw = stdout.strip()
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
        plan: AgentResult | None = None,
        requirements: RequirementsSpec | None = None,
        reviewer_notes: str = "",
        review_feedback: str = "",
        repo_roots: list[dict] | None = None,
    ) -> AgentResult:
        self._execution_context = {
            "repo_cwd": repo_cwd,
            "repo_roots": repo_roots,
            "execute_changes": execute_changes,
            "branch_name": branch_name,
            "plan": plan,
            "requirements": requirements,
            "reviewer_notes": reviewer_notes,
            "review_feedback": review_feedback,
        }
        try:
            return await self.run(issue)
        finally:
            self._execution_context = None
