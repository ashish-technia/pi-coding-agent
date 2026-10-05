# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## After making changes

Treat the documentation update as part of the change, not a follow-up. A change is not
finished until every surface below that describes the thing you touched has been updated in
the same commit. The docs here are unusually load-bearing - `docs/docs/architecture/` and
`docs/docs/agents/` describe the graph node by node, so stale pages actively mislead.

| When you change | Also update |
|---|---|
| A graph node, edge or router | `docs/docs/architecture/graph.md` and the matching `docs/docs/agents/*.md` |
| `GraphState` fields or `Status` values | `docs/docs/architecture/state.md` and the status list in `architecture/graph.md` (both enumerate them) |
| A gate or its allowed decisions | `architecture/graph.md` ("Gates and their decisions"), `operations/webhook.md` ("The reply schema"), and the checklist under *Gates, interrupts and decisions* below |
| A FastAPI route | `docs/docs/api/endpoints.md` and the API table in `README.md` |
| A setting in `config.py` | `.env.example`, `docs/docs/getting-started/configuration.md`, the Configuration section of `README.md`, and `public_view()` if it is non-secret |
| The repo list, its selection or per-repo behaviour | `docs/docs/getting-started/repositories.md`, `repos.example.json`, and the multi-repo notes in `agents/*.md` |
| The Pi runner payload, protocol or tool allowlist | `docs/docs/agents/planning-agent.md`, `agents/coding-agent.md`, and the Pi runner protocol section of `architecture/technical-design.md` |
| Review or retry behaviour | `docs/docs/operations/retry.md` |
| The checkpointer, serde allowlist or registry | `docs/docs/operations/checkpointing.md` |
| Docker, compose or the mount layout | `docs/docs/operations/docker.md` |
| Dev commands, ports or dependencies | `docs/docs/getting-started/installation.md`, `README.md`, and `.claude/launch.json` |
| Anything that contradicts this file | `CLAUDE.md` |

Adding a new docs page also means adding it to `docs/sidebars.ts`. Verify with
`cd docs && npm run build` - Docusaurus fails the build on broken internal links, which is the
only automated check these docs have. Never edit `docs/build/` or `docs/.docusaurus/`; both are
generated and git-ignored.

Keep the counts and paths in `README.md` honest too - the test count in its Development section
and the module list at the end of it are easy to leave behind.

## Commands

```bash
scripts/check.sh all                         # what CI runs: lint, types, tests, frontend, docs
scripts/check.sh lint | types | test | test-pg | frontend | docs   # one check
pytest -q                                    # full suite (61 tests); external systems faked, git runs on temp repos
pytest tests/test_workflow.py::test_plan_reject_cancels -q     # one test
PI_TEST_DATABASE_URL=postgresql://pijira:pijira@localhost:5440/pijira pytest -q   # same suite against Postgres
uv sync --extra dev                          # .venv from uv.lock; after editing deps: uv lock, commit both

python -m pi_jira_agent --port 8000 --reload # API + built SPA on :8000
python scripts/demo_server.py 8090           # everything faked; a full run finishes in seconds, no credentials

cd frontend && npm run build                 # outputs into src/pi_jira_agent/static/dist (what FastAPI serves)
cd frontend && npm run dev                   # Vite on :5173, proxies /api,/webhooks,/health to :8000
cd frontend && npm run lint                  # oxlint
cd docs && npm start                         # Docusaurus site
```

Run the server with `python -m pi_jira_agent`, not bare `uvicorn`: psycopg's async driver cannot run on
Windows' default Proactor loop, so `__main__.py` installs the selector loop from `eventloop.py`
(`tests/conftest.py` does the equivalent).

ruff lints and formats (`[tool.ruff]` in `pyproject.toml`); pyright runs in basic mode with
`reportTypedDictNotRequiredAccess` off, because `GraphState` is partial by design. pre-commit runs ruff
and gitleaks before each commit and blocks pushes to `main`. CI is `.github/workflows/ci.yml`; every job
calls `scripts/check.sh`, so add a new check there, not in the workflow.

## Working agreement

The project plan in Claude Docs is the single source of truth
(https://claude.ai/code/artifact/78083046-a888-49ef-b35b-f8ea7620df7a): requirements R-01..R-55 live in
its Requirements tab. Every change starts from a requirement on a feature branch cut from `main`
(`r-37-foundations`), commits are named `R-37: <imperative summary>`, and work reaches `main` only
through a pull request with green CI. Update the plan's status and Changelog with the change.

`.claude/launch.json` defines preview servers: `pi-jira-agent` (8000), `demo` (8091), `frontend-dev` (5173),
`docusaurus` (3001). They use `.venv/Scripts/python.exe` (created by `uv sync --extra dev`).

## Architecture

A Jira issue becomes a reviewed PR through a LangGraph `StateGraph` that pauses at four human gates.
`README.md` covers the user-facing flow; the points below are the ones that span files.

### Layers

- `graph/build.py` wires the whole graph: it constructs every client/LLM/Pi executor and adds the nodes,
  edges and conditional edges. Adding a node means editing this file and `graph/orchestrator.py`.
- `graph/nodes/` holds the node factories (`make_*`), which close over their dependencies so tests can
  substitute fakes. `graph/gates.py` holds the four pause points.
- `service.py` (`AutomationService`) owns run lifecycle: it compiles the graph with a checkpointer, runs
  each invocation as a background asyncio task keyed by issue key, validates decisions and assembles the
  status dict the UI polls.
- `workspace.py` (`RunWorkspaces`) gives each run its own git worktree per repository under `RUNS_ROOT`;
  the service removes them when a run ends. The configured clones are only ever fetched.
- `main.py` is a thin FastAPI layer over `AutomationService`, plus the SPA fallback route.
- `registry.py` is a *separate* small `runs` table (SQLite or Postgres, chosen the same way as the
  checkpointer) so `/api/runs` can list runs without scanning checkpoints. Keep both backends in sync.

### Gates, interrupts and decisions

Every pause is a LangGraph `interrupt` whose payload carries a `type`
(`requirements_approval`, `plan_approval`, `phase_gate`, `final_review`). `graph/decisions.py` is the
single source of truth for what a human may answer at each type: the pydantic `Decision` union,
`ALLOWED_ACTIONS`, the Jira `/command` parser and the help text. Both channels funnel through it —
the UI posts JSON to `/api/runs/{key}/decision`, and `channels/jira_comments.py` turns a Jira reply
into the same payload. The graph itself is channel-agnostic; `state["channel"]` only decides where
`AutomationService._after_run` announces the next pause.

`AutomationService` holds one `asyncio.Lock` per issue around every check-then-start (`start_run`,
`submit_decision`, `retry`), and every pending payload carries `gate_id` (the interrupt id). The HTTP API
requires a decision to echo it and answers `409` (`ConflictError`) for an earlier gate; the Jira channel
passes the id of the gate it parsed the command against and drops a `comment_id` it has already seen.

On resume LangGraph re-executes the interrupted node from the top, so **gate functions must be pure
before their `interrupt` call**.

Adding or changing a gate touches, at minimum: `graph/decisions.py` (model + `ALLOWED_ACTIONS` +
`command_help` + `parse_comment_command`), `graph/nodes/gates.py`, the router in `graph/orchestrator.py`,
the edges in `graph/build.py`, `_GATE_NODES` and `_STATUS_FOR_PENDING` in `service.py`,
`NODE_LABELS`/`STAGE_OF_NODE` in `graph/progress.py`, and a `*Gate.tsx` component in `frontend/src/components/`.

### Repositories

A run works in one or more repositories, listed in `repos.json` (`REPOS_CONFIG_PATH`) and loaded by
`settings.repos()`. When that file is absent, `REPO_LOCAL_PATH` becomes a single synthetic repo and
everything behaves as it did before multi-repo support — **keep that fallback working**, because it
is what the single-repo deployments and most tests rely on.

`REPOS_ROOT` (set to `/workspace` in the image) makes every clone live at `<root>/<name>` and
ignores the `path` in `repos.json`, so one file works on the host and in a container. The Docker
entrypoint then runs `python -m pi_jira_agent.repo_setup` on `serve` only — never for `demo`, which
works on throwaway repositories — to clone whatever is missing. That step is deliberately non-fatal.

`GraphState["repos"]` stores repo *names* only; `graph/build.py` builds the `RepoConfig` registry
and the per-repo `BitbucketClient` map once at startup, and nodes resolve them through
`graph/repo_context.py`; the `GitBranchClient` for a run's worktree comes from `RunWorkspaces.git()`. Consequently **editing `repos.json` needs a restart**.

A run never edits a configured clone. `prepare_workspace` (between `scope_check` and
`planning_agent`) fetches and creates a detached worktree at `origin/<target_branch>` in
`<RUNS_ROOT>/<issue key>/<repo>`, and records the commit in `GraphState["base_shas"]`. Worktree paths
are derived, never stored. `AutomationService._release_workspace` removes them when a run ends in
`done`, `failed` or `cancelled`; a `stuck_error` run keeps them, and a missing worktree raises instead
of being recreated empty.

The first selected repo (in `repos.json` order, not click order) is the *primary*: its worktree is the Pi
session's `cwd`, because `bash` has only one working directory. Everything else is addressed by
absolute path, which works because Pi's tools resolve absolute paths as given — `resolveToCwd` in
the SDK does not sandbox to `cwd`. That also means containment is only checked after the fact, in
the runner; there is no pre-tool hook in this build.

Anything per-repo is keyed by name: `diffs`, `phase_diff`, `phase_diffs`, `tree_shas`, `phase_base`, `pr_urls`.
`diffs` is the whole change so far (for the human); `phase_diff` is what the current phase added (for the
reviewer). `phase_gate` copies `tree_shas` into `phase_base` on continue; it cannot take the snapshot
itself, because a gate must stay pure. Phases slice the plan by
stage, not by repository — one coding session edits every repo in the same pass, so both sides of
a cross-repo change land and are reviewed together.

### State and persistence

`graph/state.py` defines `GraphState` (a `TypedDict`) and `initial_state()`, which must give every field a
value: a restarted issue reuses its checkpoint thread, and any key the new input omits keeps the previous
run's value (`tests/test_state.py` enforces this). Checkpoints are Postgres when `DATABASE_URL` is set,
otherwise a SQLite file at `GRAPH_CHECKPOINT_DB`. Any pydantic model stored in the state must be listed in
`make_serde()` in `graph/build.py` (`allowed_msgpack_modules`) or checkpoints fail to deserialize.

On startup `_resume_interrupted()` scans the registry and resumes runs a restart cut short (a node is due,
no pending interrupt, no task error) once each, counted in `GraphState["auto_resumes"]`; after that the run
shows `stuck_error` until someone retries it. Cancelling a background task (shutdown) records nothing, so
it looks exactly like a crash. `coding_agent` restores each worktree to the last finished pass before it
runs, so a re-run never builds on a half-applied edit.

Two status subtleties in `service.py`: a paused node has not returned yet, so the checkpointed `status` is
stale and the effective one is derived from the pending interrupt via `_STATUS_FOR_PENDING`; and a node that
raised leaves `state.next` set with a task error, which is surfaced as the synthetic status `stuck_error`
(recoverable with `POST /api/runs/{key}/retry`).

`graph/progress.py` is an in-process, non-persistent ring buffer of live sub-checkpoint activity (current
node, Pi tool calls). It is lost on restart and is not shared between workers — only checkpoints and the
registry survive.

### The Pi runner boundary

`node/pi-sdk-runner.mjs` is the only place the Pi SDK is invoked. `pi_agent.py` spawns it with `node`, writes
one JSON payload on stdin, and reads the `AgentResult` JSON from stdout; the runner streams live progress as
`@@PI {json}` lines on **stderr**, which `_run_streaming` forwards into `progress`. Anything else on stderr is
collected for the error message.

The payload's `repoRoots` lists every attached repository; with one root the runner's output is
byte-identical to the single-repo behaviour (no `repo` field, no path prefixes), which is what keeps
existing setups unaffected. `resolveRepoPath` accepts an absolute path, a `repo` field plus a
relative path, or a `repo/path` string, and rejects anything resolving outside the roots.

The same script serves three modes, selected by the payload: plan, refine (plan + `reviewerNotes`) and execute
(`executeChanges: true`). Plan mode sets `sessionOptions.tools = ["read","grep","find","ls"]` — omitting `tools`
would silently enable bash/edit/write, so that assignment is what makes plan mode read-only. After a plan is
produced, `validatePlan` rejects steps whose file does not exist or that the agent never opened with `read`,
and feeds the problems back for up to `PLAN_MAX_CORRECTIONS` rounds.

The coding agent does not produce the diff; `git_client.GitBranchClient.diff()` reads the run's worktree of
each selected repo after the agent runs. It diffs the run's base commit against `snapshot()`, a tree built
in a throwaway index, because `git diff HEAD` leaves out files the agent created. `cap_diffs()` in
`review_agent.py` trims what the review model sees to `REVIEW_MAX_DIFF_CHARS` by whole files and records
them in `review_omitted_files`, which the phase and final gates show. Nothing is committed until `pr_node`, so those worktrees
still hold every phase's changes. The coding node creates the plan's branch on its first pass only, in
every selected repo's worktree, and `pr_node` force-pushes it. Every `GitBranchClient` call made from an async node
goes through `asyncio.to_thread`: it is a blocking subprocess, and on the event loop a slow push would freeze
the API and every other run. `pr_node` must stay safe to repeat (commit if
dirty, push, find an open PR before creating one); the Jira comment and transition live in `announce_node`
so a Jira failure never re-runs the Bitbucket step.

### Configuration

`config.py` exposes a module-level `settings` singleton built from `.env`, so anything that changes the
environment (tests, `scripts/demo_server.py`) must do so *before* importing `pi_jira_agent`. Each of the four
stages resolves its own provider/model/key through `settings.stage_model(stage)`, with documented fallbacks
(planning/coding → `PI_*`, requirements → `REVIEW_*`). `public_view()` is what the Settings screen shows and
must stay free of secrets.

## Testing

`tests/conftest.py` sets the whole test environment at import time and `install_fakes()` monkeypatches every
external system: both LLM stages (`FakeLLM`, which scripts a review rejection then an approval and one
out-of-scope finding), `PiAgentExecutor.run_with_mode` (`FakeRunner`, which returns a two-phase plan and, in
execute mode, appends a line to each file the plan names), the Jira client and Bitbucket. Git is not faked:
conftest builds two real repositories (`web`, `api`), each with a bare `origin`, in a temp directory, and the
real `GitBranchClient` and worktrees run against them; `git()`, `remote_branches()`, `base_clone()` and
`runs_root()` let a test inspect the result. Tests drive real graph execution end to end and use `wait_paused()` to
poll until the background task stops. `scripts/demo_server.py` reuses the same fakes, so a change to the fakes
changes the demo too.
