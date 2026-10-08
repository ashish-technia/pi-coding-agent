"""The Pi runner process: what it can see in its environment, and that it dies with its children.

These run the real `PiAgentExecutor.run` against a small stand-in script, so the spawn,
the environment and the kill are the production code paths.
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap

import pytest

from pi_jira_agent.models import JiraIssue
from pi_jira_agent.pi_agent import PiAgentExecutor

pytestmark = pytest.mark.asyncio

_RESULT = '{"branch_name": "b", "commit_message": "c", "pr_title": "t", "pr_description": "d", "analysis": %s}'


def _executor(script, *, timeout: int = 30, passthrough: list[str] | None = None) -> PiAgentExecutor:
    return PiAgentExecutor(
        model="m",
        system_prompt="",
        api_key="provider-key",
        provider="openai",
        node_command=sys.executable,
        runner_script=str(script),
        agent_dir=".pi-agent",
        timeout_seconds=timeout,
        env_passthrough=passthrough or [],
    )


def _issue() -> JiraIssue:
    return JiraIssue(key="TEST-1", summary="s", description="d", project_key="TEST")


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in listed.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed child that nobody reaped is a zombie: gone for our purposes.
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


async def _gone(pid: int, timeout: float = 10.0) -> bool:
    for _ in range(int(timeout / 0.1)):
        if not _alive(pid):
            return True
        await asyncio.sleep(0.1)
    return False


async def test_agent_environment_holds_no_secret_but_the_model_key(tmp_path, monkeypatch):
    script = tmp_path / "print_env.py"
    script.write_text(
        f"import json, os, sys\nsys.stdin.read()\nprint({_RESULT!r} % json.dumps(json.dumps(dict(os.environ))))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-secret")
    monkeypatch.setenv("GIT_HTTPS_PASSWORD", "git-secret")
    monkeypatch.setenv("MY_TOOL_HOME", "/opt/tool")
    monkeypatch.setenv("NOT_LISTED", "nope")

    result = await _executor(script, passthrough=["MY_TOOL_HOME"]).run(_issue())
    seen = {name.upper(): value for name, value in json.loads(result.analysis).items()}

    # conftest puts every service secret in os.environ, as .env does in a deployment.
    for secret in (
        "JIRA_API_TOKEN",
        "WEBHOOK_SECRET",
        "DATABASE_URL",
        "PI_API_KEY",
        "REVIEW_API_KEY",
        "BITBUCKET_TOKEN",
        "GIT_HTTPS_PASSWORD",
        "NOT_LISTED",
    ):
        assert secret not in seen, f"{secret} reached the agent"
    assert "secret" not in json.dumps(seen)
    assert seen["PI_PROVIDER_API_KEY"] == "provider-key"
    assert seen["MY_TOOL_HOME"] == "/opt/tool", "PI_ENV_PASSTHROUGH names are let through"
    assert seen.get("PATH"), "the agent still needs to find its tools"


def _spawning_script(tmp_path):
    """A stand-in runner that starts a long-lived child, records its pid, and never finishes."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "spawn_child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import subprocess, sys, time
            sys.stdin.read()
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            open({str(pid_file)!r}, "w").write(str(child.pid))
            time.sleep(120)
            """
        ),
        encoding="utf-8",
    )
    return script, pid_file


async def _child_pid(pid_file) -> int:
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("the stand-in runner never started its child")


async def test_timeout_kills_the_whole_process_tree(tmp_path):
    script, pid_file = _spawning_script(tmp_path)
    with pytest.raises(RuntimeError, match="timed out"):
        await _executor(script, timeout=2).run(_issue())
    assert await _gone(await _child_pid(pid_file)), "a process the agent started outlived the timeout"


async def test_cancelling_the_run_kills_the_whole_process_tree(tmp_path):
    script, pid_file = _spawning_script(tmp_path)
    task = asyncio.create_task(_executor(script, timeout=120).run(_issue()))
    child = await _child_pid(pid_file)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _gone(child), "a process the agent started outlived the cancelled run"


async def test_review_sends_the_review_payload_and_parses_the_findings(tmp_path):
    script = tmp_path / "echo_review.py"
    script.write_text(
        "import json, sys\n"
        "p = json.load(sys.stdin)\n"
        "print(json.dumps({'summary': p['mode'] + ' of ' + ','.join(sorted(p['review']['diff'])),\n"
        "  'findings': [{'number': 1, 'severity': 'must', 'category': 'bug', 'claim': p['review']['rules'],\n"
        "                'file': 'a.py', 'line': 3}],\n"
        "  'resolved': [f['number'] for f in p['review']['previous']],\n"
        "  'not_reviewed': [str(p['issue'])], 'usage': {'input': 5, 'output': 1, 'cost': 0.01}}))\n",
        encoding="utf-8",
    )
    result = await _executor(script).run_review(
        "review-1",
        issue=None,
        repo_cwd=str(tmp_path),
        repo_roots=[{"name": "web", "path": str(tmp_path)}],
        diff={"web": {"base": "abc", "tree": "def"}},
        rules="No secrets",
        previous=[{"number": 4, "severity": "must", "claim": "x"}],
    )
    assert result.summary == "review of web"
    assert result.findings[0].claim == "No secrets"
    assert (result.findings[0].file, result.findings[0].line) == ("a.py", 3)
    assert result.resolved == [4]
    assert result.not_reviewed == ["None"], "a review without an issue sends none"
    assert result.usage == {"input": 5, "output": 1, "cost": 0.01}
