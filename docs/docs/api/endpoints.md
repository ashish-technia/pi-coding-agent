---
sidebar_position: 1
title: REST API
---

# REST API

Interactive documentation is served at `/docs`. All JSON.

With `AUTH_MODE=oidc` every `/api/*` request except `GET /api/auth/config` needs
`Authorization: Bearer <access token>`; without a valid one the answer is `401`. The webhooks use
the `X-Webhook-Secret` header instead. See [Sign-in](../operations/authentication.md).

## Runs (UI, flow 1)

### `POST /api/runs`

Start a run. Fetches the issue from Jira unless inline text is supplied.

```json
{ "issue_key": "WAAS-643" }
{ "issue_key": "WAAS-643", "repos": ["web", "api"] }
{ "issue_key": "DEV-1", "summary": "…", "description": "…" }
{ "issue_key": "WAAS-643", "pr_review": false }
```

`repos` names the repositories the run may work in, by `name` from `repos.json`. Omit it (or send
an empty list) to use the repos flagged `default_selected`. An unknown name is rejected with `400`.

`pr_review` switches the [whole-change review](../agents/pr-review-agent.md) before the final gate
on or off for this run. Omit it to use `PR_REVIEW_DEFAULT`.

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
| `base_shas` | repo name → the commit the run's worktree was created from |
| `decision_log` | one entry per answered gate: `gate`, `action`, `by` (`ui` or `jira:<accountId>`), `at`; final-gate entries also carry `review` (`shown`, `fix`, `left`) |
| `pr_review_enabled`, `pr_review`, `pr_review_skipped`, `fix_rounds` | whether the run reviews the whole change, the latest result (`summary`, `findings[]`, `resolved[]`, `not_reviewed[]`, `dropped_findings[]`), whether it was skipped, and fix rounds so far. The pending `final_review` payload carries the same as `pr_review`, plus `fix_available` and `max_fix_rounds` |
| `usage` | what the run spent on model calls: `by_stage[]` (`stage`, `calls`, `input`, `output`, `cache_read`, `cost_usd`, `unpriced`), totals `calls`, `input`, `output`, `cost_usd`, `unpriced_calls`, and `budget_usd` (null without a budget) |
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
| `final_review` | `{"action":"create_pr","pr_title":"…","pr_description":"…"}` · `{"action":"finish"}` · `{"action":"fix","findings":[1,3],"notes":"…"}` |

### `POST /api/runs/{key}/retry`

Resume a run stuck on a node that raised (timeout, missing key, git failure) from its last checkpoint. `409` when the run is running.

The body is optional. `{"skip_pr_review": true}` continues a run that is stuck on `pr_review`
without the review; the final gate then says the change was not reviewed. It is refused with
`400` when the run is stuck on anything else.

## Standalone reviews

A review of a branch on its own, with no run and no gates. See
[PR Review Agent](../agents/pr-review-agent.md#reviewing-a-branch-on-its-own).

### `POST /api/reviews`

```json
{ "branch": "feature/WAAS-643-retry", "repos": ["web", "api"] }
{ "branch": "feature/WAAS-643-retry", "repos": ["web"], "issue_key": "WAAS-643" }
```

`repos` are names from `repos.json`; omitted uses the repos flagged `default_selected`.
`issue_key` is optional: with it the change is judged against that Jira issue. Answers `400`
when the branch name is not one, a repository is unknown, or the branch is missing in any chosen
repository; nothing is checked out in that case. Returns the review, whose `id` looks like
`review-1a2b3c4d`.

### `GET /api/reviews`

Recent reviews, newest first: `id`, `branch`, `repos`, `issue_key`, `status` (`queued`,
`running`, `done`, `failed`, `interrupted`), `started_by`, `commits`, `running`, and the counts
`findings` and `must`.

### `GET /api/reviews/{id}`

One review with its `result` (`summary`, `findings[]`, `not_reviewed[]`, `dropped_findings[]`,
`files_changed[]`, `usage`), the `commits` it covered (`{repo: {head, base}}`), `error` when it
failed, and the live `activity` while it runs. `404` for an unknown id.

### `DELETE /api/reviews/{id}`

Deletes a finished review and its findings. `409` while it is running, `404` for an unknown id.

### `GET /api/auth/config`

Open, no token needed. `{"mode": "none"}`, or `{"mode": "oidc", "issuer", "client_id", "scope"}`:
what the UI needs to send a visitor to the issuer.

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

Parses the first line as a command for the pending gate. Comments from `JIRA_AGENT_ACCOUNT_ID` and non-command comments are ignored (`handled: false`). So is a `comment_id` that was already handled, as is a command whose `author_account_id` is not an approver (`GATE_APPROVERS`); a repeated id is how a redelivered web request is kept from answering the next gate; the ids are remembered in memory, per process.

### `POST /webhooks/jira` (classic)

Accepts the full Jira webhook payload (`webhookEvent`, `issue`) for existing setups.

## Health

`GET /health` → `{"status":"ok"}`.
