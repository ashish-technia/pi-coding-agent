"""The size cap on what the review model is shown."""


def test_cap_diffs_drops_whole_files_lockfiles_first():
    from pi_jira_agent.graph.nodes.review_agent import cap_diffs

    def file_diff(path: str, lines: int) -> str:
        body = "".join(f"+line {i}\n" for i in range(lines))
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"

    source, lock, big = file_diff("src/a.py", 5), file_diff("package-lock.json", 40), file_diff("src/big.py", 200)
    diffs = {"web": source + lock + big}

    assert cap_diffs(diffs, 0) == (diffs, []), "0 disables the cap"
    assert cap_diffs(diffs, 10**6) == (diffs, [])

    kept, omitted = cap_diffs(diffs, len(source) + len(big) + 10)
    assert omitted == ["package-lock.json"] and kept == {"web": source + big}, "the lockfile goes first"

    kept, omitted = cap_diffs(diffs, len(source) + 10)
    assert omitted == ["package-lock.json", "src/big.py"] and kept == {"web": source}, "then the largest file, whole"

    kept, omitted = cap_diffs({"web": source, "api": big}, len(source) + 10)
    assert omitted == ["api/src/big.py"] and kept == {"web": source}, "repo-qualified when several repos"
