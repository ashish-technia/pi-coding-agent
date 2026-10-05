#!/usr/bin/env bash
# Container entrypoint: prepare git for the mounted repository, then run.
#
#   serve            start the API (default)
#   demo             start the fake-backed demo server
#   <anything else>  executed verbatim, e.g. `docker compose run app bash`
set -euo pipefail

REPO="${REPO_LOCAL_PATH:-/workspace/repo}"
REPOS_CONFIG="${REPOS_CONFIG_PATH:-/app/repos.json}"

log() { printf '[entrypoint] %s\n' "$*"; }

configure_git() {
  # A bind-mounted repository is owned by the host user, which git refuses to use
  # ("dubious ownership") when the uids differ. The mount is explicitly trusted.
  git config --global --add safe.directory '*'

  # Line endings. A repository checked out on Windows has CRLF in the working tree
  # while the index holds LF; without normalisation this Linux container sees every
  # file as modified, which would poison `git diff` and `git add -A`.
  #   input  convert CRLF->LF when staging, never rewrite on checkout (default)
  #   true   also write CRLF on checkout (matches a Windows host exactly)
  #   false  no conversion (native Linux/macOS clone)
  git config --global core.autocrlf "${GIT_AUTOCRLF:-input}"

  # `git commit` fails without an identity. This is the commit author for the
  # changes the coding agent makes.
  git config --global user.name "${GIT_AUTHOR_NAME:-pi-jira-agent}"
  git config --global user.email "${GIT_AUTHOR_EMAIL:-pi-jira-agent@localhost}"

  # Non-interactive: never block on a credential prompt, fail fast instead.
  git config --global core.askPass ""
  export GIT_TERMINAL_PROMPT=0

  configure_git_credentials

  if [ -d "${HOME}/.ssh" ]; then
    # Mounted keys are read-only; accept-new avoids a prompt on first contact
    # without disabling host verification afterwards.
    git config --global core.sshCommand "ssh -o StrictHostKeyChecking=accept-new"
    log "ssh keys detected at ${HOME}/.ssh"
  fi
}

# HTTPS credentials for pushing. Skipped entirely when the remote uses SSH.
configure_git_credentials() {
  local host="${BITBUCKET_HOST:-bitbucket.org}"
  local user="" secret="" kind=""

  if [ -n "${GIT_HTTPS_USERNAME:-}" ] && [ -n "${GIT_HTTPS_PASSWORD:-}" ]; then
    user="$GIT_HTTPS_USERNAME"; secret="$GIT_HTTPS_PASSWORD"; kind="GIT_HTTPS_*"
  elif [ -n "${BITBUCKET_USERNAME:-}" ] && [ -n "${BITBUCKET_APP_PASSWORD:-}" ] \
       && [ "${BITBUCKET_USERNAME}" != "user@example.com" ] && [ "${BITBUCKET_APP_PASSWORD}" != "replace-me" ]; then
    user="$BITBUCKET_USERNAME"; secret="$BITBUCKET_APP_PASSWORD"; kind="app password"
  elif [ -n "${BITBUCKET_TOKEN:-}" ]; then
    # Repository/workspace access tokens authenticate as x-token-auth; an Atlassian
    # API token needs your account email instead. Override with GIT_HTTPS_USERNAME.
    user="${GIT_HTTPS_USERNAME:-x-token-auth}"; secret="$BITBUCKET_TOKEN"; kind="token"
  fi

  if [ -z "$secret" ]; then
    log "no HTTPS git credentials configured; pushes will need SSH keys or GIT_HTTPS_USERNAME/GIT_HTTPS_PASSWORD"
    return
  fi

  export GIT_CRED_USERNAME="$user" GIT_CRED_PASSWORD="$secret"
  git config --global "credential.https://${host}.helper" \
    '!f() { echo "username=${GIT_CRED_USERNAME}"; echo "password=${GIT_CRED_PASSWORD}"; }; f'
  # A username embedded in the remote URL wins over the helper's, which breaks auth
  # when the two disagree. This rewrite strips it.
  git config --global "url.https://${host}/.insteadOf" "https://${user}@${host}/"
  log "git credentials for https://${host}: username=${user} (${kind})"
}

check_repo() {
  if [ ! -d "$REPO" ]; then
    log "WARNING: REPO_LOCAL_PATH=$REPO does not exist. Mount the target repository there."
    return
  fi
  if [ ! -e "$REPO/.git" ]; then
    log "WARNING: $REPO is not a git repository. Planning and coding will fail."
    return
  fi

  local branch dirty
  branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  dirty="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  log "target repository: $REPO (branch: $branch, uncommitted: $dirty)"

  if [ "$dirty" -gt 200 ]; then
    log "WARNING: $dirty files look modified. That usually means a line-ending mismatch"
    log "         between the host checkout and this container. Try GIT_AUTOCRLF=true"
    log "         (Windows host) or GIT_AUTOCRLF=false (Linux/macOS host)."
  fi

  if ! git -C "$REPO" diff --quiet 2>/dev/null || [ "$dirty" -gt 0 ]; then
    log "note: the working tree is not clean; the agent's diff will include pre-existing changes"
  fi
}

# Clone whatever repos.json lists and is not on disk yet, then report each one.
# Only for `serve`: the demo works on throwaway repositories it creates itself, and
# an interactive shell should not start cloning either.
setup_repos() {
  if [ -f "$REPOS_CONFIG" ]; then
    log "repository list: $REPOS_CONFIG (clones live under REPOS_ROOT=${REPOS_ROOT:-unset})"
    # Non-fatal by design: a missing repo fails its own run with a clear error rather
    # than stopping the service from starting at all.
    python -m pi_jira_agent.repo_setup || log "WARNING: repository setup reported problems"
  else
    log "no repository list at $REPOS_CONFIG; using the single REPO_LOCAL_PATH clone"
    check_repo
  fi
}

configure_git

case "${1:-serve}" in
  serve)
    setup_repos
    log "starting API on 0.0.0.0:${APP_PORT:-8000}"
    exec python -m pi_jira_agent --host 0.0.0.0 --port "${APP_PORT:-8000}"
    ;;
  demo)
    log "starting demo server (all external systems faked) on 0.0.0.0:${APP_PORT:-8000}"
    export DEMO_HOST="${DEMO_HOST:-0.0.0.0}"
    exec python scripts/demo_server.py "${APP_PORT:-8000}"
    ;;
  *)
    exec "$@"
    ;;
esac
