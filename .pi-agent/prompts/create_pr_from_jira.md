Generate a strict JSON object with keys:

- `branch_name` (string)
- `commit_message` (string)
- `pr_title` (string)
- `pr_description` (string)
- `files_changed` (string array)

Use Jira issue details to produce a safe, reviewable PR proposal.
Do not include markdown code fences.
