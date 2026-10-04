---
sidebar_position: 1
title: Installation
---

# Installation

## Prerequisites

- Python 3.11+ and Node.js 20+
- A local clone of each target repository (`REPO_LOCAL_PATH`, or several listed in `repos.json` — see [Repositories](./repositories.md))
- API keys for the model providers you choose, a Jira API token, Bitbucket credentials
- Optional: Docker for Postgres and Redis

## Steps

```bash
git clone <this repo> && cd pi-jira-agent
python -m venv myenv && myenv\Scripts\activate          # Windows; use source myenv/bin/activate elsewhere
pip install -e ".[dev]"
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

## Run the tests

```bash
pytest -q
PI_TEST_DATABASE_URL=postgresql://pijira:pijira@localhost:5440/pijira pytest -q   # exercise Postgres
```

## Frontend development

```bash
cd frontend && npm run dev     # http://localhost:5173, proxies /api to :8000 (VITE_API_TARGET overrides)
```
