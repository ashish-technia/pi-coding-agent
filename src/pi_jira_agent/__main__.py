"""Run the API server: ``python -m pi_jira_agent [--port 8000] [--reload]``.

Prefer this over calling uvicorn directly: it installs the event loop the Postgres
driver needs on Windows (see eventloop.py). Equivalent uvicorn command:

    uvicorn pi_jira_agent.main:app --loop pi_jira_agent.eventloop:loop_factory
"""

from __future__ import annotations

import argparse

from .eventloop import LOOP_SPEC


def main() -> None:
    parser = argparse.ArgumentParser(description="pi-jira-agent API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development).")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "pi_jira_agent.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=LOOP_SPEC,
    )


if __name__ == "__main__":
    main()
