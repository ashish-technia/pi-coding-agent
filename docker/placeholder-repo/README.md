Mounted at `/workspace/repo` only when `TARGET_REPO_PATH` is unset.

The single-repository setup bind-mounts a host clone there. With a `repos.json`
the container clones each repository to `/workspace/<name>` instead and nothing
reads this directory — it exists so compose does not need a required variable.
