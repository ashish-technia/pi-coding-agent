---
sidebar_position: 1
title: Installation
---

# Installation

## Prerequisites

- Python 3.12, Node.js 22.19 or later (the Pi SDK requires it) and [uv](https://docs.astral.sh/uv/) (`pip install uv` works)
- A local clone of each target repository (`REPO_LOCAL_PATH`, or several listed in `repos.json` — see [Repositories](./repositories.md))
- API keys for the model providers you choose, a Jira API token, Bitbucket credentials
- Optional: Docker for Postgres and Redis

## Steps

```bash
git clone <this repo> && cd pi-jira-agent
uv sync --extra dev                                        # .venv with the exact versions in uv.lock
.venv\Scripts\activate                                     # Windows; use source .venv/bin/activate elsewhere
pre-commit install                                         # ruff + gitleaks before commits, guard on pushes to main
npm install                                                # Pi SDK for the Node runner
cd frontend && npm install && npm run build && cd ..       # React UI → src/pi_jira_agent/static/dist
docker compose up -d                                       # optional Postgres + Redis
copy .env.example .env                                     # then fill in secrets
python -m pi_jira_agent --port 8000 --reload
```

Open http://localhost:8000.

On Windows always start with `python -m pi_jira_agent`; it selects the event loop the Postgres driver needs. With SQLite (`DATABASE_URL` empty) plain `uvicorn pi_jira_agent.main:app` also works.

## Verify without credentials

```bash
python scripts/demo_server.py
```

Serves the same API and UI on http://localhost:8090 with every external system faked. A full run (requirements → scope warning → phased plan → review retry → PR) completes in seconds.

## Run the checks

`scripts/check.sh` runs every check exactly as CI does (use Git Bash on Windows):

```bash
scripts/check.sh lint       # ruff lint + format check
scripts/check.sh types      # pyright (basic mode)
scripts/check.sh test       # pytest on SQLite
scripts/check.sh test-pg    # pytest on Postgres (default: the compose service on :5440)
scripts/check.sh frontend   # oxlint + type-checked Vite build
scripts/check.sh docs       # Docusaurus build; fails on broken links
scripts/check.sh all        # everything; test-pg only when PI_TEST_DATABASE_URL is set
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs the same checks on every push, scans the full
history for secrets, and builds the Docker image on `main`. Dependabot proposes grouped dependency
updates weekly.

## Dependencies

Python versions are pinned in `uv.lock` and installed with `uv sync`; the Docker image installs from
the same file. Add or change a dependency in `pyproject.toml`, then run `uv lock` and commit both files.
Node dependencies are pinned by each `package-lock.json`; the Pi SDK is pinned to an exact version.

## Frontend development

```bash
cd frontend && npm run dev     # http://localhost:5173, proxies /api to :8000 (VITE_API_TARGET overrides)
```
