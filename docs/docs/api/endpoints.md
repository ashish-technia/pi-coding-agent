---
sidebar_position: 1
title: REST API
---

# REST API

Interactive documentation is served at `/docs`. All JSON.

## Runs (UI, flow 1)

### `POST /api/runs`

Start a run. Fetches the issue from Jira unless inline text is supplied.

```json
{ "issue_key": "WAAS-643" }
{ "issue_key": "WAAS-643", "repos": ["web", "api"] }
{ "issue_key": "DEV-1", "summary": "…", "description": "…" }
```

`repos` names the repositories the run may work in, by `name` from `repos.json`. Omit it (or send
an empty list) to use the repos flagged `default_selected`. An unknown name is rejected with `400`.

Returns the run status. Starting an issue that is already running or paused returns its current status without restarting it.

### `GET /api/runs`

Recent runs from the registry: `issue_key`, `summary`, `status`, `channel`, `created_at`, `updated_at`, `running`.

### `GET /api/runs/{key}`

Full status:

| Field | Meaning |
|---|---|
| `status`, `running`, `node`, `node_label`, `stage` | where the run is |
| `pending` | the gate waiting for a decision (`type`, `gate_id`, plus its payload) or `null`. `gate_id` is unique to this pause |
| `issue_details` | summary, description, comments, labels, url |
| `requirements`, `requirements_original`, `scope_check` | requirements stage |
| `plan_result`, `execution_mode`, `phase_index`, `phases_total` | plan stage |
| `repos` | repo names this run works in, primary first |
| `code_result`, `diffs`, `phase_diffs`, `review_approved`, `review_feedback`, `iteration`, `max_iterations` | coding/review; `diffs` maps repo name → the run's worktree diff, new files included; `phase_diffs` holds one such map per accepted phase, each with only that phase's changes |
| `pr_title`, `pr_urls` | delivery; `pr_urls` maps repo name → pull request URL |
| `error`, `stuck_on` | present when `status == "stuck_error"`: a node raised, or the run was interrupted by a restart and already had its one automatic resume |
| `auto_resumes` | how often the service resumed the run by itself after a restart (0 or 1) |

### `POST /api/runs/{key}/decision`

One decision, validated against `pending.type`. Wrong action → `400`.

Every body must also carry `"gate_id"`, copied from `pending.gate_id` (missing → `422`). It names the
pause being answered: the same kind of gate comes back (requirements after a revise, the phase gate
between phases), so without it a double-click or a resent request could answer the next one. A
`gate_id` that is no longer pending, or a decision that arrives while the run is running, gets
`409`. Two decisions sent at the same moment resume the run once; the other gets `409`.

| Pending type | Body examples |
|---|---|
| `requirements_approval` | `{"action":"approve","requirements":{…},"acknowledge_scope":false}` · `{"action":"revise","notes":"…"}` · `{"action":"cancel"}` |
| `plan_approval` | `{"action":"approve","mode":"all"}` · `{"action":"approve","mode":"phased"}` · `{"action":"refine","notes":"…"}` · `{"action":"reject"}` |
| `phase_gate` | `{"action":"continue"}` · `{"action":"stop"}` |
| `final_review` | `{"action":"create_pr","pr_title":"…","pr_description":"…"}` · `{"action":"finish"}` |

### `POST /api/runs/{key}/retry`

Resume a run stuck on a node that raised (timeout, missing key, git failure) from its last checkpoint. `409` when the run is running.

### `GET /api/config`

Effective configuration without secrets: models per stage, repository, delivery flags, review rules, persistence backends.

## Jira ingress (flow 2)

Both require the `x-webhook-secret` header equal to `WEBHOOK_SECRET`.

### `POST /webhooks/jira/trigger`

```json
{ "issue_key": "WAAS-643" }
```

Starts a run on the `jira` channel (queued when `USE_QUEUE=true`).

### `POST /webhooks/jira/comment`

```json
{ "issue_key": "WAAS-643", "comment_body": "/approve phased", "author_account_id": "5f…", "comment_id": "10412" }
```

Parses the first line as a command for the pending gate. Comments from `JIRA_AGENT_ACCOUNT_ID` and non-command comments are ignored (`handled: false`). So is a `comment_id` that was already handled, which is how a redelivered web request is kept from answering the next gate; the ids are remembered in memory, per process.

### `POST /webhooks/jira` (classic)

Accepts the full Jira webhook payload (`webhookEvent`, `issue`) for existing setups.

## Health

`GET /health` → `{"status":"ok"}`.
