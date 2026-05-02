# Bitbucket PR Formatting Skill

Use this skill to produce review-friendly Bitbucket pull request content.

## PR quality bar

- Title ties directly to Jira issue.
- Description is scannable and actionable.
- Risks are explicit.
- Test plan is concrete and check-list friendly.

## Description template

Context:
- What problem this change addresses.

Proposed changes:
- 2-5 bullets describing code and behavior updates.

Risks:
- Data, compatibility, performance, or rollout risks.

Test plan:
- [ ] Unit or integration tests
- [ ] Manual happy-path verification
- [ ] Regression checks

## Guardrails

- Avoid over-claiming test coverage.
- Avoid vague words like "several" or "various".
- Keep line lengths practical for Bitbucket rendering.
