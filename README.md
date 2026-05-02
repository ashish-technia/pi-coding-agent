# pi-jira-agent

Reference project for this workflow:

Jira webhook -> FastAPI (auth + filter) -> Queue (optional) -> Pi agent (SDK) -> Bitbucket PR -> Jira update

## What this includes

- FastAPI endpoint for Jira webhook events.
- Shared-secret authentication on webhook requests.
- Project-key filtering (`ALLOWED_PROJECTS`).
- Optional async queue worker for decoupled processing.
- Service layer that:
  - executes Pi agent logic,
  - creates a Bitbucket PR,
  - comments on the Jira issue and optionally transitions it.

## Quick start

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:
   - `pip install -e .`
   - `npm install`
3. Copy env file:
   - `copy .env.example .env` (Windows)
   - `cp .env.example .env` (Unix)
4. Fill all required secrets and URLs in `.env`.
5. Run API:
   - `uvicorn pi_jira_agent.main:app --host 0.0.0.0 --port 8080 --reload`

Default behavior is dry-run style:

- `CREATE_PR=false` logs generated changes and skips Bitbucket PR creation.
- When `CREATE_PR=true`, you can enable local branch prep with:
  - `PREPARE_BRANCH_BEFORE_PR=true`
  - `REPO_LOCAL_PATH=<local repo path>`
- To let Pi modify repository files before commit/PR:
  - `PI_EXECUTE_CHANGES=true`

Health check:

- `GET /health`

Webhook endpoint:

- `POST /webhooks/jira`
- Header: `x-webhook-secret: <WEBHOOK_SECRET>`

## Jira webhook payload expectation

The app expects a Jira issue payload containing:

- `webhookEvent`
- `issue.key`
- `issue.fields.summary`
- `issue.fields.description` (optional)
- `issue.fields.project.key`

## Pi SDK integration (implemented)

This project now executes Pi via the official SDK package
`@mariozechner/pi-coding-agent` from [pi.dev](https://pi.dev/).

- Node runner: `node/pi-sdk-runner.mjs`
- Python bridge: `src/pi_jira_agent/pi_agent.py`

Flow:

1. FastAPI receives Jira event.
2. Python bridge spawns Node runner.
3. Node runner creates a Pi SDK session using `createAgentSession(...)`.
4. Pi returns JSON PR metadata.
5. Optional: Pi execution mode applies code changes in `REPO_LOCAL_PATH`.
6. Python continues commit/push/Bitbucket/Jira orchestration.

If `CREATE_PR=false`, step 5 only logs generated change metadata:

- branch name
- commit message
- PR title/description
- files changed

When `CREATE_PR=true` and `PREPARE_BRANCH_BEFORE_PR=true`, the service also:

1. fetches remote
2. checks out and fast-forwards target branch
3. creates/resets source branch
4. pushes source branch to remote

When `PI_EXECUTE_CHANGES=true`, the service additionally:

1. runs Pi in repository execution mode at `REPO_LOCAL_PATH`
2. checks whether changes were produced
3. commits and pushes generated changes

You can customize the Pi behavior by editing:

- `PI_PROVIDER` (e.g. `openai`, `anthropic`)
- `PI_MODEL`
- `PI_SYSTEM_PROMPT`
- `PI_AGENT_DIR` (default `.pi-agent`)

## Pi agent files (AGENTS + skills + prompts)

The project includes Pi context files in `.pi-agent`:

- `.pi-agent/AGENTS.md`
- `.pi-agent/skills/jira-pr/SKILL.md`
- `.pi-agent/skills/bitbucket-pr/SKILL.md`
- `.pi-agent/prompts/create_pr_from_jira.md`

`node/pi-sdk-runner.mjs` passes `agentDir` into `createAgentSession(...)`,
so these files are loaded during SDK execution.

## Production recommendations

- Put FastAPI behind API gateway with IP allow-listing.
- Validate Jira webhook signature if available in your setup.
- Replace in-memory queue with Redis/RQ, Celery, or SQS worker.
- Add idempotency key per Jira event to avoid duplicate PR creation.
- Add structured logging and tracing for event lifecycle.
