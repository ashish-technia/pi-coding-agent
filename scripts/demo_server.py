"""Run the API with every external system faked, for UI development and demos.

    python scripts/demo_server.py            # http://localhost:8090

The Pi runner, LLMs, Jira and Bitbucket are replaced by the same fakes the test suite
uses, and git runs for real against throwaway repositories in a temp directory, so a
full run (requirements -> plan -> phases -> review -> PR) completes in seconds without
credentials or model calls. Review rejects the first attempt of each
phase once, so the retry loop is visible too.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.gettempdir()) / f"pi-jira-agent-demo-{uuid.uuid4().hex[:6]}"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ.update(
    {
        "WEBHOOK_SECRET": "demo-secret",
        "ALLOWED_PROJECTS": "",
        "PI_API_KEY": "x",
        "BITBUCKET_BASE_URL": "https://bitbucket.invalid",
        "BITBUCKET_WORKSPACE": "demo",
        "BITBUCKET_REPO_SLUG": "repo",
        "JIRA_BASE_URL": "https://jira.invalid",
        "JIRA_EMAIL": "agent@example.com",
        "JIRA_API_TOKEN": "x",
        "REVIEW_API_KEY": "x",
        "USE_QUEUE": "false",
        "REPO_LOCAL_PATH": str(_TMP),
        "PR_CREATION_ENABLED": "true",
        "JIRA_COMMENTS_ENABLED": "false",
        "JIRA_COMMENT_CHANNEL_ENABLED": "false",
        "REVIEW_MAX_ITERATIONS": "2",
        "GRAPH_CHECKPOINT_DB": str(_TMP / "checkpoints.sqlite"),
        "DATABASE_URL": "",
        "REDIS_URL": "",
    }
)

# NOTE: importing conftest also applies its os.environ block, which overrides the values
# set above. That is where the demo's two repos (web + api) and its paths come from.
from tests.conftest import install_fakes  # noqa: E402


class _Patcher:
    """Minimal stand-in for pytest's monkeypatch."""

    def setattr(self, obj, name, value):
        setattr(obj, name, value)


def main() -> None:
    import uvicorn

    from pi_jira_agent.eventloop import LOOP_SPEC

    fakes = install_fakes(_Patcher())
    # Realistic demo behaviour: first review of each phase is rejected, then approved.
    fakes["llm"].review_script = [False, True, False, True, True]
    fakes["llm"].scope_flags = True
    # The PR review finds two things first; after a fix round only the lesser one is left.
    lesser = {"severity": "should", "category": "tests", "claim": "No test covers giving up after the last attempt."}
    fakes["runner"].review_script = [
        [
            {
                "severity": "must",
                "category": "bug",
                "claim": "The retry loop never stops on a 503, so a dead server hangs the caller.",
                "suggestion": "Give up after 3 attempts and raise the last error.",
                "file": "src/client.py",
                "line": 2,
            },
            lesser,
        ],
        [lesser],
    ]

    from pi_jira_agent import main as app_main

    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(os.environ.get("DEMO_PORT", "8090"))
    print(f"Demo server with fakes on port {port}  (temp dir {_TMP})")
    # Loopback locally; containers set DEMO_HOST=0.0.0.0 so the port is reachable.
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    uvicorn.run(app_main.app, host=host, port=port, log_level="info", loop=LOOP_SPEC)


if __name__ == "__main__":
    main()
