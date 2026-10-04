"""Shared fixtures: an isolated settings environment and fakes for every external system."""

import json
import os
import tempfile
import uuid
from pathlib import Path

# Settings are a module-level singleton, so the environment must be ready before any
# pi_jira_agent import. Values here never reach a real service.
_TMP = Path(tempfile.gettempdir()) / f"pi-jira-agent-tests-{uuid.uuid4().hex[:8]}"
_TMP.mkdir(parents=True, exist_ok=True)

# Two repos, but only "web" is selected by default, so a test that says nothing about
# repos behaves exactly like the single-repo setup. test_workflow opts into both.
_REPOS = _TMP / "repos.json"
for _name in ("web", "api"):
    (_TMP / _name).mkdir(parents=True, exist_ok=True)
_REPOS.write_text(
    json.dumps(
        {
            "repos": [
                {
                    "name": "web",
                    "path": str(_TMP / "web"),
                    "bitbucket_repo_slug": "web-repo",
                    "default_selected": True,
                    "properties": {"language": "typescript"},
                },
                {
                    "name": "api",
                    "path": str(_TMP / "api"),
                    "bitbucket_repo_slug": "api-repo",
                    "target_branch": "main",
                    "properties": {"language": "python"},
                },
            ]
        }
    ),
    encoding="utf-8",
)
os.environ.update(
    {
        "WEBHOOK_SECRET": "test-secret",
        "ALLOWED_PROJECTS": "",
        "PI_API_KEY": "x",
        "BITBUCKET_BASE_URL": "https://bitbucket.invalid",
        "BITBUCKET_WORKSPACE": "ws",
        "BITBUCKET_REPO_SLUG": "repo",
        "JIRA_BASE_URL": "https://jira.invalid",
        "JIRA_EMAIL": "agent@example.com",
        "JIRA_API_TOKEN": "x",
        "REVIEW_API_KEY": "x",
        "USE_QUEUE": "false",
        "REPO_LOCAL_PATH": str(_TMP),
        "REPOS_CONFIG_PATH": str(_REPOS),
        "PREPARE_BRANCH_BEFORE_PR": "true",
        "PR_CREATION_ENABLED": "true",
        "JIRA_COMMENTS_ENABLED": "false",
        "REVIEW_MAX_ITERATIONS": "2",
        "GRAPH_CHECKPOINT_DB": str(_TMP / "checkpoints.sqlite"),
        # Opt in to the Postgres checkpointer/registry with PI_TEST_DATABASE_URL.
        "DATABASE_URL": os.environ.get("PI_TEST_DATABASE_URL", ""),
        "REDIS_URL": "",
        # The Docker image sets REPOS_ROOT; tests that need it patch settings directly.
        "REPOS_ROOT": "",
        "REVIEW_RULES_PATH": str(_TMP / "no-rules.md"),
    }
)

import asyncio  # noqa: E402
import sys  # noqa: E402

if sys.platform == "win32":
    # psycopg's async driver cannot run on the Proactor loop that Windows uses by default.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402

from pi_jira_agent import bitbucket_client, git_client, jira_client, pi_agent  # noqa: E402
from pi_jira_agent.graph import build as build_mod  # noqa: E402
from pi_jira_agent.graph.nodes.review_agent import ReviewVerdict  # noqa: E402
from pi_jira_agent.models import (  # noqa: E402
    AgentResult,
    JiraComment,
    JiraIssue,
    PlanPhase,
    PlanStep,
    PullRequestResult,
    RequirementsSpec,
    ScopeCheck,
    ScopeFinding,
)


class FakeLLM:
    """Stands in for a LangChain chat model; returns canned objects per schema."""

    def __init__(self):
        self.review_calls = 0
        self.scope_calls = 0
        self.framing_calls = 0
        self.review_script: list[bool] = [False, True]  # reject first, then approve
        self.scope_flags = True  # first scope check flags an out-of-scope item
        self.last_prompts: dict[str, str] = {}

    def with_structured_output(self, schema):
        llm = self

        class _Bound:
            async def ainvoke(self, messages):
                text = "\n".join(getattr(m, "content", "") for m in messages)
                if schema is RequirementsSpec:
                    llm.framing_calls += 1
                    llm.last_prompts["framing"] = text
                    return RequirementsSpec(
                        title="Add retry to client",
                        problem="Client gives up on transient errors.",
                        goals=["Retry transient HTTP errors"],
                        acceptance_criteria=["A 503 is retried up to 3 times"],
                        in_scope=["HTTP client"],
                        out_of_scope=["UI changes"],
                        sources=["description", "comment by Sam"],
                    )
                if schema is ScopeCheck:
                    llm.scope_calls += 1
                    llm.last_prompts["scope"] = text
                    if llm.scope_flags:
                        return ScopeCheck(
                            findings=[
                                ScopeFinding(
                                    item="Also add caching",
                                    kind="added",
                                    verdict="out_of_scope",
                                    reason="The issue never mentions caching.",
                                )
                            ]
                        )
                    return ScopeCheck()
                if schema is ReviewVerdict:
                    idx = min(llm.review_calls, len(llm.review_script) - 1)
                    approved = llm.review_script[idx]
                    llm.review_calls += 1
                    llm.last_prompts["review"] = text
                    return ReviewVerdict(
                        approved=approved,
                        comments="Looks good." if approved else "Missing null check on line 12.",
                    )
                raise AssertionError(f"unexpected schema {schema}")

        return _Bound()


class FakeRunner:
    """Replaces PiAgentExecutor.run_with_mode; records calls and returns plans/results."""

    def __init__(self):
        self.calls: list[dict] = []
        self.phases = True

    async def __call__(
        self,
        executor,
        issue,
        *,
        repo_cwd,
        execute_changes,
        branch_name=None,
        plan=None,
        requirements=None,
        reviewer_notes="",
        review_feedback="",
        repo_roots=None,
    ):
        self.calls.append(
            {
                "execute": execute_changes,
                "branch": branch_name,
                "plan_steps": [s.file for s in plan.plan_steps] if plan else None,
                "requirements": requirements.title if requirements else None,
                "reviewer_notes": reviewer_notes,
                "review_feedback": review_feedback,
                "repo_cwd": repo_cwd,
                "repo_roots": [r["name"] for r in (repo_roots or [])],
            }
        )
        names = [r["name"] for r in (repo_roots or [])]
        label = lambda s: f"{s.repo}/{s.file}" if s.repo else s.file  # noqa: E731

        if execute_changes:
            return AgentResult(
                branch_name=branch_name or "feature/x",
                commit_message="TEST-1: implement",
                pr_title="TEST-1 implement retry",
                pr_description="did it",
                files_changed=[label(s) for s in plan.plan_steps] if plan else ["a.py"],
                plan_steps=plan.plan_steps if plan else [],
            )
        steps = [
            PlanStep(file="src/client.py", action="modify", change="add retry loop", evidence="no retry today"),
            PlanStep(file="tests/test_client.py", action="create", change="test retry", evidence="no tests"),
        ]
        # Spread the steps over the attached repos, the way the real runner tags them.
        if len(names) > 1:
            for index, step in enumerate(steps):
                step.repo = names[index % len(names)]
        return AgentResult(
            branch_name="feature/TEST-1-retry",
            commit_message="TEST-1: add retry",
            pr_title="TEST-1 add retry",
            pr_description="plan",
            files_changed=[s.file for s in steps],
            analysis="client.py has no retry",
            plan_steps=steps,
            verification=["pytest -q"],
            notes_response="Addressed." if reviewer_notes else "",
            phases=[
                PlanPhase(name="Core", description="retry loop", step_indexes=[0]),
                PlanPhase(name="Tests", description="cover it", step_indexes=[1]),
            ]
            if self.phases
            else [],
        )


def install_fakes(monkeypatch) -> dict:
    """Patch every external system. `monkeypatch` only needs a `setattr(obj, name, value)`."""
    llm = FakeLLM()
    runner = FakeRunner()
    prs: list[dict] = []
    git_calls: list[str] = []

    monkeypatch.setattr(build_mod, "make_requirements_llm", lambda: llm)
    monkeypatch.setattr(build_mod, "make_review_llm", lambda: llm)

    async def _run_with_mode(self, issue, **kwargs):
        return await runner(self, issue, **kwargs)

    monkeypatch.setattr(pi_agent.PiAgentExecutor, "run_with_mode", _run_with_mode)

    async def fake_get_issue(self, key):
        return JiraIssue(
            key=key,
            summary="Add retry to client",
            description="Client should retry transient errors.",
            project_key=key.split("-")[0],
            reporter="Ana",
            comments=[JiraComment(author="Sam", created="2026-09-01", body="Max 3 attempts please.")],
            url=f"https://jira.invalid/browse/{key}",
        )

    async def fake_add_comment(self, key, body):
        return "1"

    monkeypatch.setattr(jira_client.JiraClient, "get_issue", fake_get_issue)
    monkeypatch.setattr(jira_client.JiraClient, "add_comment", fake_add_comment)

    # Git calls are tagged with the repo directory name, so multi-repo tests can tell which
    # clone each commit/push belongs to.
    monkeypatch.setattr(
        git_client.GitBranchClient,
        "prepare_branch",
        lambda self, **kw: git_calls.append(f"prepare:{self.repo_path.name}"),
    )
    monkeypatch.setattr(git_client.GitBranchClient, "has_changes", lambda self: True)
    monkeypatch.setattr(
        git_client.GitBranchClient,
        "commit_all",
        lambda self, msg: git_calls.append(f"commit:{self.repo_path.name}:{msg}"),
    )
    monkeypatch.setattr(
        git_client.GitBranchClient, "push_branch", lambda self, b: git_calls.append(f"push:{self.repo_path.name}:{b}")
    )
    monkeypatch.setattr(git_client.GitBranchClient, "ahead_count", lambda self, **kw: 1)

    def fake_diff(self, target_branch=None):
        n = len(runner.calls)
        repo = self.repo_path.name
        return (
            f"diff --git a/{repo}/src/client.py b/{repo}/src/client.py\n"
            f"--- a/{repo}/src/client.py\n"
            f"+++ b/{repo}/src/client.py\n"
            "@@ -1,4 +1,9 @@\n"
            " import httpx\n"
            " \n"
            " def get(url):\n"
            "-    return httpx.get(url)\n"
            f"+    for attempt in range({n}, 3):\n"
            "+        response = httpx.get(url)\n"
            "+        if response.status_code != 503:\n"
            "+            return response\n"
            "+    return response\n"
        )

    monkeypatch.setattr(git_client.GitBranchClient, "diff", fake_diff)

    async def fake_create_pr(self, *, title, description, source_branch, destination_branch):
        prs.append(
            {
                "title": title,
                "source": source_branch,
                "dest": destination_branch,
                "slug": self.repo_slug,
                "description": description,
            }
        )
        return PullRequestResult(pr_id=7, pr_url=f"https://bitbucket.invalid/{self.repo_slug}/pr/7")

    monkeypatch.setattr(bitbucket_client.BitbucketClient, "create_pull_request", fake_create_pr)

    return {"llm": llm, "runner": runner, "prs": prs, "git": git_calls}


@pytest.fixture
def fakes(monkeypatch):
    return install_fakes(monkeypatch)


@pytest.fixture
async def service(fakes):
    from pi_jira_agent.service import AutomationService

    svc = AutomationService()
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


async def wait_paused(svc, key, timeout=10.0):
    """Poll until the background task finishes (paused at a gate, done, or stuck)."""
    import asyncio

    for _ in range(int(timeout / 0.02)):
        status = await svc.get_status(key)
        if not status["running"]:
            return status
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {key} still running after {timeout}s")
