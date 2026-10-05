---
sidebar_position: 2
title: Configuration
---

# Configuration

All settings come from environment variables or `.env` (see `.env.example`). The Settings page in the UI shows the effective values without secrets.

## Models per stage

| Stage | Variables | Fallback | Runs in |
|---|---|---|---|
| requirements | `REQUIREMENTS_MODEL_PROVIDER`, `REQUIREMENTS_MODEL`, `REQUIREMENTS_API_KEY` | review settings | LangChain chat call |
| planning | `PLANNING_MODEL_PROVIDER`, `PLANNING_MODEL`, `PLANNING_API_KEY` | `PI_PROVIDER`, `PI_MODEL`, `PI_API_KEY` | Pi harness (read-only tools) |
| coding | `CODING_MODEL_PROVIDER`, `CODING_MODEL`, `CODING_API_KEY` | `PI_*` | Pi harness (full tools) |
| review | `REVIEW_MODEL_PROVIDER`, `REVIEW_MODEL`, `REVIEW_API_KEY` | provider env var (`OPENAI_API_KEY` …) | LangChain chat call |

Chat providers: `openai`, `anthropic`, `google` (needs `langchain-google-genai`). Pi providers: anything Pi's registry supports (OpenAI, Anthropic, Google, Bedrock, Azure, OpenAI-compatible).

`PI_THINKING_LEVEL` (`off` … `xhigh`) applies to planning and coding; `PI_TIMEOUT_SECONDS` (default 600) bounds one Pi call. `PI_ENV_PASSTHROUGH` is a comma-separated list of environment variable names the Pi process may inherit. By default it starts with a fixed base only (`PATH`, `HOME`, locale and temp variables; the Windows equivalents on Windows) plus its model key, so none of this service's secrets are in its environment. Add what the agent still needs, such as `HTTPS_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS` or `JAVA_HOME`. `MAX_CONCURRENT_RUNS` (default 2) caps how many Pi sessions run at once across all runs: a run waiting at a gate holds no slot, and a run that finds every slot taken waits with the label *Waiting for a free agent slot*.

## Sign-in

`AUTH_MODE` is `none` (default; only served on `127.0.0.1`) or `oidc`, which needs `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_AUDIENCE`. `OIDC_SCOPE` and `OIDC_JWKS_URL` are optional. See [Sign-in](../operations/authentication.md).

## Repository and delivery

| Variable | Meaning |
|---|---|
| `REPOS_CONFIG_PATH` | JSON file listing every repository the agents may work in (default `repos.json`). See [Repositories](./repositories.md) |
| `REPOS_ROOT` | when set, clones live at `<root>/<name>` and the `path` in `repos.json` is ignored. Docker sets `/workspace` and clones there |
| `BITBUCKET_HOST` | git host used to derive a clone URL when a repo has no `clone_url` (default `bitbucket.org`) |
| `REPO_LOCAL_PATH` | local clone the agents plan against and edit, used when `repos.json` is absent |
| `GIT_REMOTE_NAME`, `BITBUCKET_TARGET_BRANCH` | remote and base branch |
| `RUNS_ROOT` | where each run's git worktrees live, as `<root>/<issue key>/<repo name>` (default `data/runs`). They hold uncommitted work while a run waits at a gate, so keep it on storage that survives restarts |
| `PR_CREATION_ENABLED` | show *Create PR* and allow commit/push/PR (`CREATE_PR` is accepted as an alias) |
| `BITBUCKET_BASE_URL`, `BITBUCKET_WORKSPACE`, `BITBUCKET_REPO_SLUG`, `BITBUCKET_TOKEN` or `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` | Bitbucket Cloud API. `BITBUCKET_REPO_SLUG` and `BITBUCKET_TARGET_BRANCH` are per-repo defaults that `repos.json` can override |

## Review

- `REVIEW_MAX_ITERATIONS` — coding/review cycles allowed per phase.
- `REVIEW_MAX_DIFF_CHARS` — largest diff, in characters, sent to the review model (default 200000; 0 = no limit). Above it whole files are left out, lockfiles and generated files first, and the phase and final gates say which.
- `REVIEW_RULES_PATH` — Markdown with `Must` and `Should` sections; the reviewer rejects on any `Must` violation.

## Jira

`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (the agent's service account in flow 2), `JIRA_TRANSITION_DONE_ID`, `JIRA_COMMENTS_ENABLED`, `ALLOWED_PROJECTS`.

`GATE_APPROVERS` (default `reporter,assignee`) says who may answer a gate with a Jira comment: the issue's `reporter`, its `assignee`, and any `account:<Atlassian accountId>`. A command from anyone else is ignored and answered once; an empty value means nobody. It does not apply to decisions made in the UI.

Flow 2 only: `JIRA_COMMENT_CHANNEL_ENABLED`, `JIRA_AGENT_ACCOUNT_ID`, `JIRA_TRIGGER_LABEL`, `WEBHOOK_SECRET`.

## Persistence and queue

| Variable | Effect |
|---|---|
| `DATABASE_URL` | Postgres DSN → `AsyncPostgresSaver` checkpoints plus the `runs` registry table; empty → SQLite at `GRAPH_CHECKPOINT_DB` |
| `REDIS_URL` | Redis list job queue; empty → in-memory queue |
| `USE_QUEUE`, `QUEUE_MAX_SIZE` | whether inbound triggers are queued |

`docker compose up -d` starts a Postgres on `localhost:5440` and Redis on `localhost:6390` matching `.env.example`.
