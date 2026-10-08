# pi-jira-agent

Turns a Jira issue into a reviewed pull request with a human in the loop at every stage.

```
Jira issue ─► requirements ─► plan (reads the code) ─► code ─► review ─► final diff ─► PR
               ▲ approve/       ▲ approve / refine /     ▲ per phase   ▲ retry loop   ▲ title + create
               revise/cancel      reject, phased?          (optional)
```

Two ways to drive it:

- **Flow 1 (UI):** enter an issue key in the web app; every pause is a screen with Approve / Revise / Reject.
- **Flow 2 (Jira comments):** a label or assignment triggers the run; every pause is posted as a Jira comment by the agent's account and humans reply with `/approve`, `/revise …`, `/pr <title>` and so on. The workflow is identical; only the channel differs.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph `StateGraph` + Postgres (or SQLite) checkpointer | pauses survive restarts and can wait for days |
| Coding harness | Pi SDK (`@earendil-works/pi-coding-agent` 1.1.0, pinned) | multi-provider; read-only tool allowlist gives a true plan mode |
| Requirements & review | LangChain chat models (OpenAI / Anthropic / Google) | structured output per stage, provider chosen per stage |
| API | FastAPI | REST for the UI and for Jira Automation web requests |
| UI | React + Vite + TypeScript SPA | wizard, editable requirements, diff viewer, PR dialog |
| Queue | in-memory or Redis | decouples Jira triggers from execution |

## Quick start (Docker)

The image carries Python, Node, the Pi SDK, git and the built UI. The target repository is mounted, and every setting comes from `.env`.

```bash
cp .env.example .env               # fill in secrets
cp repos.example.json repos.json   # list your repositories; the container clones them
docker compose up -d --build       # app + Postgres + Redis  →  http://localhost:8000
```

For a single repository, skip `repos.json` and point `TARGET_REPO_PATH` at a local clone instead.

See [Docker deployment](docs/docs/operations/docker.md) for how repositories are cloned, git credentials for pushing, line-ending handling, and demo mode (`docker run --rm -p 8000:8000 pi-jira-agent:latest demo` needs no credentials).

## Quick start (local)

```bash
uv sync --extra dev && .venv\Scripts\activate          # Windows; exact versions from uv.lock
pre-commit install                                      # ruff + gitleaks hooks, guard on pushes to main
npm install                                             # Pi runner deps
cd frontend && npm install && npm run build && cd ..    # builds into src/pi_jira_agent/static/dist
docker compose up -d                                    # Postgres + Redis (optional; SQLite works without)
copy .env.example .env                                  # fill in secrets
python -m pi_jira_agent --port 8000 --reload
```

Open http://localhost:8000. Use `python -m pi_jira_agent` rather than bare `uvicorn` on Windows: the Postgres driver needs the selector event loop.

**Try it without any credentials:** `python scripts/demo_server.py` starts the API on port 8090 with the Pi runner, models, Jira, git and Bitbucket replaced by fakes, so a full run completes in seconds.

## Configuration

Everything is in `.env` (see `.env.example`). The important groups:

- **Per-stage models:** `REQUIREMENTS_*`, `PLANNING_*`, `CODING_*`, `REVIEW_*` (`_MODEL_PROVIDER`, `_MODEL`, `_API_KEY`). Planning and coding fall back to `PI_PROVIDER` / `PI_MODEL` / `PI_API_KEY`.
- **Repositories:** `REPOS_CONFIG_PATH` points at `repos.json`, the list of clones a run may work in (copy `repos.example.json`); `REPO_LOCAL_PATH` is the single-clone fallback when that file is absent. Each run works in its own git worktree under `RUNS_ROOT` (default `data/runs`); the clones are only fetched. Plus `BITBUCKET_*`.
- **Review rules:** `REVIEW_RULES_PATH` points at a Markdown file of must-check items (default `review-rules.md`).
- **Side effects:** `PR_CREATION_ENABLED` gates commit/push/PR; `JIRA_COMMENTS_ENABLED` gates status comments.
- **Persistence:** `DATABASE_URL` (Postgres) or the SQLite fallback; `REDIS_URL` for the queue.
- **Sign-in:** `AUTH_MODE` is `none` (default, served on `127.0.0.1` only) or `oidc` with `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_AUDIENCE`. `docker compose` starts Keycloak as the issuer and needs `KEYCLOAK_ADMIN_PASSWORD`.
- **Flow 2:** `GATE_APPROVERS` (who may answer a gate from Jira; default `reporter,assignee`), `JIRA_COMMENT_CHANNEL_ENABLED`, `JIRA_AGENT_ACCOUNT_ID`, `JIRA_TRIGGER_LABEL`, `WEBHOOK_SECRET`.

The Settings page in the UI shows the effective, non-secret configuration.

## How a run works

1. **Fetch** the issue: summary, description (ADF converted to text) and every comment.
2. **Requirements agent** frames the problem, goals, acceptance criteria, in/out of scope, assumptions and open questions, citing where each came from. You edit and approve; if your edits add things the issue never asked for, a **scope check** flags them and you either remove them or keep them knowingly. "Revise" sends notes back to the analyst.
3. **Planning agent** runs Pi in plan mode (read, grep, find, ls only). It must open every file it plans to change; the runner validates paths against the repo and against what was actually read, and sends problems back for correction. The plan has per-file steps with evidence, verification commands, and optional phases. Approve all at once or phase by phase; "Refine" revises the existing plan and answers your notes.
4. **Coding agent** runs Pi with full tools on the approved plan (or the current phase), re-reading files and running verification with bash.
5. **Review agent** checks the diff against the acceptance criteria, the phase's plan steps and your review rules. Rejections go back to the coder with the feedback, up to `REVIEW_MAX_ITERATIONS` per phase.
6. **Final review** shows the full diff. Create the PR with your own title, or finish without one.

### More than one repository

Tick several repositories on the start form and the whole run spans them: one plan covering all of
them, one coding pass that edits them together, a review that reads every diff side by side, and
one pull request per repository that actually changed — same branch name in each. Configure the
list in `repos.json`; see [Repositories](docs/docs/getting-started/repositories.md). With no
`repos.json` the agent works in the single `REPO_LOCAL_PATH` clone exactly as before.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/runs` | start a run (`{"issue_key": "WAAS-1"}`; optional `repos`, inline `summary`/`description`) |
| `GET` | `/api/runs` | recent runs |
| `GET` | `/api/runs/{key}` | full status, including the pending decision |
| `POST` | `/api/runs/{key}/decision` | `{"action": ...}` validated against the pending gate |
| `POST` | `/api/runs/{key}/retry` | resume a run stuck on an error |
| `GET` | `/api/config` | effective configuration (no secrets) |
| `GET` | `/api/auth/config` | open: how the UI signs in (`mode`, issuer, client id) |
| `POST` | `/webhooks/jira/trigger` | flow 2: `{"issue_key"}` from a Jira Automation rule |
| `POST` | `/webhooks/jira/comment` | flow 2: `{"issue_key","comment_body","author_account_id","comment_id"}` |

Interactive docs at `/docs`.

## Jira setup for flow 2

See `docs/docs/operations/webhook.md`. In short: a service account with an API token, one Automation rule that calls `/webhooks/jira/trigger` when the label is added or the issue is assigned to the agent, and one that calls `/webhooks/jira/comment` on new comments. Both send `x-webhook-secret`.

## Documentation

The Docusaurus site under `docs/` (`cd docs && npm start`) holds the full documentation. Start with **Architecture → Technical design** (`docs/docs/architecture/technical-design.md`): system context, component architecture, runtime topology, module map, code flow with sequence diagrams, state and persistence, the Pi runner protocol, decisions and channels, error handling, security, testing, and roadmap.

## Development

```bash
scripts/check.sh all                        # lint, types, 81 tests, runner tests, frontend and docs builds, as CI runs them
scripts/check.sh test-pg                    # the suite against Postgres (compose service on :5440)
cd frontend && npm run dev                  # Vite dev server on :5173 proxying to :8000
cd docs && npm start                        # documentation site
```

Repository layout: `src/pi_jira_agent/graph/` (nodes, gates, routers, decisions, repo context), `service.py` (run lifecycle), `main.py` (API), `config.py` (settings and the `repos.json` loader), `channels/` (Jira comment channel), `node/pi-sdk-runner.mjs` (the only place Pi is invoked), `frontend/` (SPA), `tests/`, `docs/`.
