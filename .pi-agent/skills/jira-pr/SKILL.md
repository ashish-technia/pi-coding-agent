# Jira to PR Skill

Use this skill when transforming a Jira issue into branch/commit/PR metadata.

## Inputs expected

- Jira key
- Project key
- Summary
- Description
- Reporter (optional)

## Method

1. Infer likely implementation scope from summary and description.
2. Propose a concise branch name:
   - `feature/<jira-key-lower>-<slug>`
3. Write a commit message:
   - `<JIRA_KEY>: <imperative summary>`
4. Write PR title:
   - `<JIRA_KEY> - <human readable summary>`
5. Write PR description with sections:
   - Context
   - Proposed changes
   - Risks
   - Test plan

## Safety checks

- If description is empty, keep scope minimal and mention assumptions.
- If requirements conflict, surface open questions in PR description.
- Keep language factual and short.
