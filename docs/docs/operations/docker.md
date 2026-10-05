---
sidebar_position: 0
title: Docker deployment
---

# Running in Docker

The image carries everything a run needs: the FastAPI service, the built React UI, Python 3.12, Node 22 with the Pi SDK, and git. The repository the agent works in is **mounted**, never baked in, and every setting comes from the environment.

## Quick start

```bash
cp .env.example .env          # fill in the secrets
cp repos.example.json repos.json   # list the repositories; the container clones them
docker compose up -d --build
```

For a single repository you can skip `repos.json` and point `TARGET_REPO_PATH` at a local clone
instead.

The UI is at `http://localhost:8000`. `docker compose logs -f app` shows startup, the detected repository, and live agent activity.

The stack is three services:

| Service | Purpose | Host port |
|---|---|---|
| `app` | API, UI, graph, Pi runner | `APP_PORT` (8000) |
| `postgres` | checkpoints and the run registry | `POSTGRES_PORT` (5440) |
| `redis` | job queue | `REDIS_PORT` (6390) |

## Mounting the target repository

```yaml
volumes:
  - ${TARGET_REPO_PATH}:/workspace/repo
```

`TARGET_REPO_PATH` is a host path to an **existing clone**; compose refuses to start without it. Inside the container it is always `/workspace/repo`, which is what `REPO_LOCAL_PATH` is set to. The mount is read-write by design: the coding agent edits files there, and you can inspect its work from the host at any time.

The clone must have a remote configured, be on a branch, and ideally be clean. The entrypoint logs the branch and the number of uncommitted files at startup, because the agent's diff includes anything already modified.

## Repositories in Docker

A container does not need a bind mount per repository: it **clones them itself**. On `serve` the
entrypoint reads the repository list and clones anything missing, then logs each repo's branch and
uncommitted count.

Three pieces make that work, all set by the image already:

| Setting | Value | Why |
|---|---|---|
| `REPOS_CONFIG_PATH` | `/app/repos.json` | the list, bind-mounted from the project root |
| `REPOS_ROOT` | `/workspace` | every clone lives at `/workspace/<name>`, so the **host** paths inside `repos.json` are ignored and one file serves both host and container |
| `pijira_workspace` volume | `/workspace` | clones survive restarts; `docker compose down -v` drops them and they are re-cloned |

So the only thing to prepare is `repos.json` in the project root — the same one you use on the host:

```json
{
  "repos": [
    { "name": "waas-server", "path": "C:/projects/technia/waas-server",
      "bitbucket_repo_slug": "waas-server", "target_branch": "develop", "default_selected": true },
    { "name": "widget-box", "path": "C:/projects/technia/widget-box",
      "bitbucket_repo_slug": "widget-box", "target_branch": "develop" }
  ]
}
```

The clone URL defaults to `https://${BITBUCKET_HOST}/${BITBUCKET_WORKSPACE}/<slug>.git`; set
`clone_url` per repo to override it (a different host, or SSH). Cloning uses the same credentials
the entrypoint configures for pushing, so a private repo needs `BITBUCKET_TOKEN` or
`BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` — or a mounted SSH key with an `ssh://` URL.

```bash
docker compose up -d --build
docker compose logs -f app        # [repo-setup] lines show each clone
```

A first start clones everything, so it takes as long as the repositories are big. Later starts
reuse the volume.

### Reusing a host clone instead

To avoid re-cloning something you already have, bind-mount it over that repository's slot. The
directory name must match the repo's `name`, and the bind wins over the volume for that path:

```yaml
volumes:
  - pijira_workspace:/workspace
  - C:/projects/technia/waas-server:/workspace/waas-server
```

The entrypoint then sees a git repository there and skips cloning it. This is also how you work
against uncommitted local changes.

### Single repository, no list

Without a `repos.json`, nothing changes from before: `TARGET_REPO_PATH` is bind-mounted at
`/workspace/repo`, `REPO_LOCAL_PATH` points there, and the entrypoint's original check reports its
branch and cleanliness.

### Line endings

A checkout made on Windows has CRLF in the working tree while the index holds LF. A Linux container comparing the two sees *every file* as modified, which would make `git diff` enormous and `git add -A` commit the whole repository. `GIT_AUTOCRLF` controls this:

| Value | Use when |
|---|---|
| `input` (default) | you are unsure; converts CRLF→LF when staging, never rewrites on checkout |
| `true` | the host clone was made on Windows with `core.autocrlf=true` |
| `false` | the clone is native Linux or macOS |

The entrypoint warns when more than 200 files look modified, which is the signature of the wrong setting. Compare `git status --porcelain | wc -l` on the host and in the container; the numbers should match.

## Configuration

Settings resolve in this order, highest first:

1. `environment:` in `docker-compose.yml` — the container-internal values that must differ from host ones (`REPO_LOCAL_PATH`, `DATABASE_URL`, `REDIS_URL`, the SQLite path, the review-rules path).
2. `env_file: .env` — everything else: API keys, Jira and Bitbucket credentials, model choices, feature flags.
3. Image defaults baked into the `Dockerfile`.

So one `.env` serves both host and container runs: the host-facing `DATABASE_URL=…localhost:5440…` stays as it is, and compose overrides it with `postgres:5432` inside the network.

To run against SQLite and the in-memory queue instead, set both to empty strings in the compose `environment:` block.

Editing `.env` requires `docker compose up -d app` to take effect. `review-rules.md` and `.pi-agent/` are bind-mounted, so changes there apply to the next run with no restart.

### Docker-specific variables

| Variable | Meaning |
|---|---|
| `TARGET_REPO_PATH` | host path to the clone for a single-repository setup, mounted at `/workspace/repo`. Not needed when `repos.json` exists |
| `REPOS_CONFIG_PATH` | container path to the repository list (default `/app/repos.json`) |
| `REPOS_ROOT` | where clones live (default `/workspace`); makes the host paths in `repos.json` irrelevant in the container |
| `RUNS_ROOT` | where each run's git worktrees live (default `/app/data/runs`, on the `pijira_data` volume) |
| `APP_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `KEYCLOAK_PORT` | host ports |
| `KEYCLOAK_ADMIN_PASSWORD` | admin password for the bundled Keycloak; **required**, compose refuses to start without it |
| `AUTH_MODE`, `OIDC_*` | sign-in; compose defaults them to the bundled Keycloak. See [Sign-in](./authentication.md) |
| `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` | commit identity for the agent's commits |
| `GIT_AUTOCRLF` | line-ending handling, see above |
| `GIT_HTTPS_USERNAME`, `GIT_HTTPS_PASSWORD` | explicit git credentials, overriding the Bitbucket ones |
| `SSH_DIR` | host `.ssh` directory, if you uncomment the SSH mount |

## Git credentials for pushing

The entrypoint configures a credential helper for HTTPS remotes, choosing the first that applies:

1. `GIT_HTTPS_USERNAME` + `GIT_HTTPS_PASSWORD`
2. `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` (ignored while they hold the `.env.example` placeholders)
3. `BITBUCKET_TOKEN`, as `x-token-auth`

The username matters and depends on the credential type:

- **Repository, project or workspace access token** → `x-token-auth` (the default).
- **Atlassian API token** (`ATCT…`, created from your Atlassian account) → your **account email**; set `GIT_HTTPS_USERNAME` to it.
- **App password** → your Bitbucket username.

A username embedded in the remote URL (`https://someone@bitbucket.org/…`) would override the helper's and break authentication, so the entrypoint rewrites such URLs to drop it.

The startup log states which username it will use. Verify before a real run:

```bash
docker compose exec app git -C /workspace/repo fetch origin
```

To push over SSH instead, uncomment the `.ssh` mount in `docker-compose.yml` and leave the HTTPS variables unset. Keys are mounted read-only and host verification uses `accept-new`.

## Operating

```bash
docker compose logs -f app                  # startup + live agent activity
docker compose exec app bash                # shell in the container
docker compose exec app python -m pytest -q # the suite, inside the image
docker compose restart app                  # after editing .env
docker compose down                         # stop (volumes survive)
docker compose down -v                      # stop and delete checkpoints
```

**Demo mode** runs the whole stack with every external system faked, so you can click through the UI without credentials or model calls:

```bash
docker run --rm -p 8000:8000 pi-jira-agent:latest demo
```

## Image design

Two stages. The first builds the SPA with Node and emits `static/dist`. The second is `python:3.12-slim` with Node 22 added from NodeSource, the Pi SDK installed from `package-lock.json`, and the Python dependencies installed with `uv sync` from `uv.lock` (the versions CI tested), with the project editable so that the runner-path lookup (`Path(__file__).parents[2]`) resolves to `/app` and finds `node/pi-sdk-runner.mjs`.

- **Non-root**: runs as `app` (uid 1000), which matches the first user on most Linux hosts so a bind-mounted repository stays writable. On a host with a different uid, uncomment `user:` in compose.
- **tini as PID 1**: reaps the Pi child process and passes SIGTERM to uvicorn.
- **Healthcheck**: polls `/health`; compose reports the container healthy only once the API answers.
- **Volumes**: `pijira_data` holds each run's git worktrees and the SQLite checkpoint when Postgres is disabled, `pijira_pgdata` holds Postgres.

## Limits in containers

- **Worktrees live in the container's volume.** Each run works in its own worktree under `RUNS_ROOT`, so concurrent runs on one repository do not interfere, but the worktrees are only visible to this `app` container; keep a single replica. A bind-mounted host clone will list these worktrees with container paths in `git worktree list` on the host; `git worktree prune` there is harmless once the runs have ended.
- **The agent sees a minimal environment.** Variables from `.env` and the compose file, including `GIT_HTTPS_PASSWORD` and the Jira and Bitbucket tokens, are not passed to the Pi process. If the agent's `bash` needs a proxy or a tool home, name the variable in `PI_ENV_PASSTHROUGH`. The mounted `.env` file itself is still readable inside the container.
- **Activity events are in-process.** Restarting `app` clears the live activity feed; the durable record is the checkpoint, so runs resume where they paused.
- **Sign-in uses the bundled Keycloak in development mode** (HTTP, embedded database). The app container itself refuses to start without sign-in, but see [Sign-in](./authentication.md) before publishing either port beyond a trusted network; the usual answer is a reverse proxy with TLS in front of it.
