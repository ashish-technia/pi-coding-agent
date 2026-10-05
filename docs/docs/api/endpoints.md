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
| `pending` | the gate waiting for a decision (`type` plus its payload) or `null` |
| `issue_details` | summary, description, comments, labels, url |
| `requirements`, `requirements_original`, `scope_check` | requirements stage |
| `plan_result`, `execution_mode`, `phase_index`, `phases_total` | plan stage |
| `repos` | repo names this run works in, primary first |
| `code_result`, `diffs`, `review_approved`, `review_feedback`, `iteration`, `max_iterations` | coding/review; `diffs` maps repo name → the run's worktree diff, new files included |
| `pr_title`, `pr_urls` | delivery; `pr_urls` maps repo name → pull request URL |
| `error`, `stuck_on` | present when `status == "stuck_error"` |

### `POST /api/runs/{key}/decision`

One decision, validated against `pending.type`. Wrong action → `400`.

| Pending type | Body examples |
|---|---|
| `requirements_approval` | `{"action":"approve","requirements":{…},"acknowledge_scope":false}` · `{"action":"revise","notes":"…"}` · `{"action":"cancel"}` |
| `plan_approval` | `{"action":"approve","mode":"all"}` · `{"action":"approve","mode":"phased"}` · `{"action":"refine","notes":"…"}` · `{"action":"reject"}` |
| `phase_gate` | `{"action":"continue"}` · `{"action":"stop"}` |
| `final_review` | `{"action":"create_pr","pr_title":"…","pr_description":"…"}` · `{"action":"finish"}` |

### `POST /api/runs/{key}/retry`

Resume a run stuck on a node that raised (timeout, missing key, git failure) from its last checkpoint.

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
{ "issue_key": "WAAS-643", "comment_body": "/approve phased", "author_account_id": "5f…" }
```

Parses the first line as a command for the pending gate. Comments from `JIRA_AGENT_ACCOUNT_ID` and non-command comments are ignored (`handled: false`).

### `POST /webhooks/jira` (classic)

Accepts the full Jira webhook payload (`webhookEvent`, `issue`) for existing setups.

## Health

`GET /health` → `{"status":"ok"}`.
