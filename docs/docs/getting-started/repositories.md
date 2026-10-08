---
sidebar_position: 3
title: Repositories
---

# Repositories

One Jira issue often needs a change in more than one repository: an endpoint in the API and the
call site in the web app. A run can attach several local clones, and the agents read and edit all
of them in one pass.

## Configuring the list

`REPOS_CONFIG_PATH` (default `repos.json`) points at a JSON file listing every repository the
agents may work in. Start from `repos.example.json`:

```json
{
  "repos": [
    {
      "name": "web",
      "path": "C:/projects/technia/web-app",
      "bitbucket_repo_slug": "web-app",
      "target_branch": "develop",
      "default_selected": true,
      "properties": { "language": "typescript", "tests": "npm test" }
    },
    {
      "name": "api",
      "path": "C:/projects/technia/api-service",
      "bitbucket_repo_slug": "api-service",
      "target_branch": "main",
      "properties": { "language": "python", "tests": "pytest -q" }
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Identifier used everywhere else: the prefix on plan-step paths, the key in the diff and PR maps, the value the UI checkboxes send. Must be unique |
| `path` | yes* | Local clone the agents read and edit. Relative paths are resolved to absolute at load. Ignored when `REPOS_ROOT` is set (Docker), where the clone is always `<root>/<name>` |
| `clone_url` | no | Git URL to clone from when the path does not exist yet. Defaults to `https://<BITBUCKET_HOST>/<BITBUCKET_WORKSPACE>/<slug>.git` |
| `bitbucket_repo_slug` | no | Defaults to `BITBUCKET_REPO_SLUG` |
| `target_branch` | no | PR destination for this repo. Defaults to `BITBUCKET_TARGET_BRANCH` |
| `default_selected` | no | Ticked by default in the UI picker. Defaults to `false` |
| `properties` | no | Free-form key/value facts handed to the planning and coding agents |

`properties` is the place for anything the agents should know that they cannot infer quickly —
the test command, the stack, a warning about a legacy area. It is quoted verbatim into the prompt.

**The list is read once, at startup.** Editing `repos.json` needs a restart, because the git and
Bitbucket clients are built with it.

### A worktree per run

The agents never edit the clone at `path`. When a run reaches planning, the service fetches each
selected repository and creates a detached git worktree at `origin/<target_branch>` under
`<RUNS_ROOT>/<issue key>/<repo name>`. Planning and coding read and edit that worktree, so two
issues on the same repository never see each other's changes, and the clone stays clean. The
worktrees and the local run branch are removed when the run ends (pull request created, finished,
cancelled or failed); a run stuck on an error keeps them so it can be retried.

If the fetch fails (an expired token, no network), the run still starts, from the last commit that
was fetched, and its activity feed carries a warning that it may be behind the remote. The same
credential problem will stop the push when the pull request is created.

A fresh worktree has no installed dependencies and no ignored local files such as `node_modules`
or a local `.env`.

### One file for the host and the container

Host paths like `C:/projects/technia/waas-server` mean nothing inside a container. Rather than
keeping a second copy of the list, the container sets `REPOS_ROOT=/workspace`, which makes every
clone live at `/workspace/<name>` and ignores the `path` field. The same `repos.json` therefore
works in both places — on the host its paths are used, in Docker the names are.

The Docker entrypoint then **clones whatever is missing** before the API starts, using `clone_url`
(or one derived from the Bitbucket workspace and slug) and the same git credentials it configures
for pushing. So a container needs the list and credentials, not a bind mount per repository. See
[Docker deployment](../operations/docker.md#repositories-in-docker).

Cloning is deliberately non-fatal: if one repository cannot be cloned the service still starts, the
failure is logged, and only the runs that need that repository fail.

If the file is missing, the single `REPO_LOCAL_PATH` clone is used as the only repository, and
everything behaves exactly as it did before multi-repo support existed.

## Picking repos for a run

- **UI:** the start form shows a checkbox per configured repository, pre-ticked from
  `default_selected`. The picker is hidden when only one repo is configured.
- **API:** `POST /api/runs` takes `"repos": ["web", "api"]`. Omit it for the defaults.
- **Flow 2:** `POST /webhooks/jira/trigger` takes the same optional `repos` list, so a Jira
  Automation rule can choose them.

The selection is stored on the run as repo *names*, in `repos.json` order. That order decides the
**primary** repository regardless of the order the boxes were ticked.

## What the primary repository means

The Pi coding harness has one working directory, because `bash` has one working directory. The
primary repo is it. Everything else is addressed by absolute path, which works because Pi's
tools resolve absolute paths as given — the same idea as Claude Code's `--add-dir`.

Two consequences:

- A bare relative path in a tool call resolves inside the primary repo only. The agents are told
  to use absolute paths for the others.
- Verification commands for a non-primary repo have to `cd` there first. The coding prompt says so.

## How a multi-repo run behaves

1. **Planning** runs once, read-only, with every selected repo attached. One plan covers them all,
   and each step records the repo it belongs to, so a contract change and its caller are planned
   together rather than in ignorance of each other.
2. **Coding** runs once per phase, not once per repo — a single agent session edits every repo in
   the same pass, so both sides of a cross-repo change land together.
3. **Review** sees one labelled diff per repo in the same prompt. This matters: judged alone, a
   new call in the web app looks like a reference to something that does not exist.
4. **Delivery** creates the same branch name in every repo and opens **one pull request per repo
   that actually changed**. Repos with no changes are skipped rather than getting an empty PR.
   Each description notes that it is part of a multi-repo change and names the repos involved;
   the Jira comment lists every URL.

Phases slice by stage, not by repository.

## Limits worth knowing

- **Pull requests cannot merge atomically.** Bitbucket has no notion of a linked PR set, so the
  descriptions cross-reference by repo name and merging in a sensible order stays a human job.
- **Planning across repositories costs context and time.** Two large codebases in plan mode is a
  lot of reading; raise `PI_TIMEOUT_SECONDS` and tick only the repos the issue really touches.
- **File tools are contained; the shell is not yet.** Pi's tools accept any absolute path, so the
  runner checks every tool call before it runs: `edit` and `write` outside the attached
  repositories are refused, and in plan mode so are `read`, `grep`, `find` and `ls`. A refused
  call shows as "blocked" in the activity panel and the agent carries on. `bash` in execute mode
  only passes a short denylist (no `git push`, `git commit`, `git remote` or network clients), so
  a shell command can still reach outside until runs get a sandbox. The runner also still
  verifies afterwards that every reported path lies inside an attached repo. Keep `repos.json`
  to repositories the agent is genuinely allowed to change.
