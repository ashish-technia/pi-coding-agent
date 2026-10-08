---
sidebar_position: 3
title: Jira setup (flow 2)
---

# Driving runs from Jira

In flow 2 nobody opens the UI: a label or assignment starts the run, and every feedback request is a Jira comment from the agent's own account. Humans reply with a command on the first line of a comment.

## 1. Create the agent's Jira account

1. Create an Atlassian **service account** (no license seat is consumed) or a normal user such as `ai-agent@yourcompany.com`, and add it to the Jira site.
2. Give it a project role with **Browse Projects, Add Comments, Edit Issues, Transition Issues** and **Assignable User** on every project in `ALLOWED_PROJECTS`.
3. Create an **API token** for it with only the Jira scopes it needs.
4. Note its `accountId` (Jira → People → the user's profile URL ends in it, or `GET /rest/api/3/myself` with its token).

Put these in `.env`:

```
JIRA_EMAIL=ai-agent@yourcompany.com
JIRA_API_TOKEN=…
JIRA_AGENT_ACCOUNT_ID=5f1234…
JIRA_COMMENT_CHANNEL_ENABLED=true
JIRA_TRIGGER_LABEL=ai-agent
WEBHOOK_SECRET=<long random string>
```

## 2. Expose the API over HTTPS

Jira Cloud must reach `/webhooks/jira/*`. Use a tunnel (Cloudflare Tunnel, ngrok) for development and an API gateway with IP allow-listing for production. Only the two webhook paths need to be public.

## 3. Automation rule: start a run

Project settings → Automation → Create rule.

- **Trigger:** *Issue assigned* (to the agent user) **or** *Field value changed* → Labels, with condition `{{issue.labels}}` contains `ai-agent`. Add an issue condition so it only fires once, e.g. status not in (`AI in progress`).
- **Action:** *Send web request*
  - URL: `https://<host>/webhooks/jira/trigger`
  - Method: `POST`, body: *Custom data*
  - Headers: `Content-Type: application/json`, `x-webhook-secret: <WEBHOOK_SECRET>` (mark hidden)
  - Body: `{"issue_key": "{{issue.key}}"}`

  To aim a rule at specific repositories, add them by name from `repos.json`:
  `{"issue_key": "{{issue.key}}", "repos": ["web", "api"]}`. Omitted, the run uses the repos
  flagged `default_selected`. See [Repositories](../getting-started/repositories.md).

## 4. Automation rule: relay replies

- **Trigger:** *Issue commented*
- **Conditions:** issue has label `ai-agent`; comment author is not the agent (`{{comment.author.accountId}}` does not equal the agent's id); optionally `{{comment.body}}` starts with `/`.
- **Action:** *Send web request* to `https://<host>/webhooks/jira/comment` with the same headers and body

```json
{
  "issue_key": "{{issue.key}}",
  "comment_body": {{comment.body.asJsonString}},
  "author_account_id": "{{comment.author.accountId}}",
  "comment_id": "{{comment.id}}"
}
```

## 5. The reply schema

The agent includes the valid commands in every comment it posts. Full list:

| Pending stage | Commands |
|---|---|
| requirements | `/approve`, `/revise <notes>`, `/cancel` |
| plan | `/approve`, `/approve phased`, `/revise <notes>`, `/reject` |
| between phases | `/continue`, `/stop` |
| final review | `/pr <pull request title>`, `/finish`, and while the PR review has findings to act on: `/fix` (every `must` finding) or `/fix 1 3 <optional notes>` |

Anything else is ignored and, if the agent cannot apply a command, it replies with the reason.

### Who may answer

Only the accounts named by `GATE_APPROVERS` may answer a gate from Jira: by default the issue's
reporter and assignee, as they were when the run fetched the issue. Add specific people with
`account:<Atlassian accountId>`. A command from anyone else is ignored; the agent says so once per
person per gate and stays silent after that. The rule's body must therefore send
`author_account_id`; a reply without it is never applied. A run started in the UI with an inline
issue has no reporter or assignee id, so only `account:` entries can answer it from Jira.

A command only answers the gate that was pending when it arrived. Send `comment_id` in the rule's
body (as above): Jira Automation can deliver a web request more than once, and without the id a
repeated `/approve` or `/continue` would answer the next gate too.

## Alternative: registered webhooks

Instead of Automation you can register a webhook via the REST API with a secret; Jira then signs each delivery with HMAC SHA-256 in `X-Hub-Signature`. The classic `/webhooks/jira` endpoint accepts the full issue payload but does not yet verify that signature. Automation rules are simpler, need no Jira admin, and send only the fields you choose.

## Operational notes

- Runs triggered from Jira are visible in the UI too; an operator can retry a stuck run from there.
- Requirements edits are UI-only today; from Jira use `/revise <notes>` to change them.
- Set `USE_QUEUE=true` and `REDIS_URL` so a burst of triggers is buffered and survives restarts.
