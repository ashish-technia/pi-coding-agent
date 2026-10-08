# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: build the React SPA.
# vite.config.ts writes to ../src/pi_jira_agent/static/dist relative to frontend/,
# so the output lands at /build/src/pi_jira_agent/static/dist.
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: runtime. Python for the API and the graph, Node for the Pi harness,
# git for the mounted target repository.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_MAJOR=22 \
    HOME=/home/app

# git: the agent works in a mounted clone. openssh-client: pushes over SSH.
# curl/gnupg: needed to add the NodeSource repository. tini: proper PID 1 so the
# Pi child process is reaped and SIGTERM reaches uvicorn.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git openssh-client tini \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get purge -y gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Non-root. uid/gid 1000 matches the first user on most Linux hosts, so a bind
# mounted repository stays writable. Override with `user:` in compose if yours differs.
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app app

WORKDIR /app

# Pi SDK for the Node runner. Copied first so dependency layers cache.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev

# Python dependencies, installed from uv.lock so the image runs exactly the versions
# CI tested. uv installs the project editable, so PiAgentExecutor's project-root
# lookup (Path(__file__).parents[2]) resolves to /app and finds node/pi-sdk-runner.mjs.
# The dev extra (pytest) lets the suite run inside the exact runtime image; set
# INSTALL_DEV=false for a slimmer production build.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"
ARG INSTALL_DEV=true
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_DEV" = "true" ]; then uv sync --frozen --extra dev; \
    else uv sync --frozen; fi

# Application files the runtime reads at request time.
COPY node/ ./node/
COPY .pi-agent/ ./.pi-agent/
COPY review-rules.md ./
# repos.jso[n] is optional - the bracket glob makes a missing file a no-op, and
# repos.example.json guarantees the COPY has at least one source.
COPY repos.example.json repos.jso[n] ./
# scripts/ + tests/ back the `demo` command (the stack with every external faked).
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Built SPA, served by FastAPI from src/pi_jira_agent/static/dist.
COPY --from=frontend /build/src/pi_jira_agent/static/dist ./src/pi_jira_agent/static/dist

# /app/data holds each run's worktrees (and the SQLite checkpoint when DATABASE_URL is
# unset); /workspace holds the
# repositories - cloned to /workspace/<name> by the entrypoint, or bind-mounted there.
# Both are created owned by app so a fresh named volume inherits that ownership.
RUN mkdir -p /app/data /workspace/repo \
 && sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && chown -R app:app /app /workspace /home/app

USER app

# The commit the image was built from, recorded in every run's manifest (R-39):
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) .
ARG GIT_SHA=unknown
ENV AGENT_GIT_SHA=$GIT_SHA

ENV REPO_LOCAL_PATH=/workspace/repo \
    REPOS_CONFIG_PATH=/app/repos.json \
    REPOS_ROOT=/workspace \
    GRAPH_CHECKPOINT_DB=/app/data/graph_checkpoints.sqlite \
    RUNS_ROOT=/app/data/runs \
    PI_NODE_COMMAND=node \
    PI_RUNNER_SCRIPT=node/pi-sdk-runner.mjs \
    PI_AGENT_DIR=.pi-agent \
    REVIEW_RULES_PATH=/app/review-rules.md \
    APP_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
