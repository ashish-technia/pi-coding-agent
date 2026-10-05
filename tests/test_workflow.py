"""End-to-end graph behaviour with every external system faked."""

import uuid

import pytest

from tests.conftest import base_clone, git, remote_branches, runs_root, wait_paused

pytestmark = pytest.mark.asyncio


def _key() -> str:
    return f"TEST-{uuid.uuid4().hex[:6].upper()}"


async def test_full_flow_phased_with_scope_warning_review_retry_and_pr(service, fakes):
    key = _key()
    await service.start_run(key)

    # 1. Requirements framed from issue + comments, run pauses for approval.
    status = await wait_paused(service, key)
    assert status["status"] == "pending_requirements"
    assert status["pending"]["type"] == "requirements_approval"
    assert status["requirements"]["title"] == "Add retry to client"
    assert "Max 3 attempts please." in fakes["llm"].last_prompts["framing"], "comments must reach the analyst"

    # 2. Human edits requirements adding an out-of-scope goal -> scope check flags it.
    edited = dict(status["requirements"])
    edited["goals"] = edited["goals"] + ["Also add caching"]
    await service.submit_decision(key, {"action": "approve", "requirements": edited})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_requirements"
    assert status["scope_check"]["findings"][0]["verdict"] == "out_of_scope"
    assert fakes["runner"].calls == [], "planning must not start while scope is unresolved"

    # 3. Human removes the flagged item and approves -> planning runs against the requirements.
    edited["goals"] = [g for g in edited["goals"] if g != "Also add caching"]
    fakes["llm"].scope_flags = False
    await service.submit_decision(key, {"action": "approve", "requirements": edited})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_plan"
    assert status["pending"]["type"] == "plan_approval"
    assert status["phases_total"] == 2
    plan_call = fakes["runner"].calls[-1]
    assert plan_call["execute"] is False and plan_call["requirements"] == "Add retry to client"

    # 4. Refine the plan -> previous plan + notes reach the planner, reply comes back.
    await service.submit_decision(key, {"action": "refine", "notes": "Keep it minimal"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_plan"
    assert fakes["runner"].calls[-1]["reviewer_notes"] == "Keep it minimal"
    assert status["plan_result"]["notes_response"] == "Addressed."

    # 5. Approve phased -> phase 1 only (src/client.py) is coded; review rejects then approves.
    await service.submit_decision(key, {"action": "approve", "mode": "phased"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_phase"
    assert status["pending"]["type"] == "phase_gate"
    coding_calls = [c for c in fakes["runner"].calls if c["execute"]]
    assert len(coding_calls) == 2, "one rejected attempt plus one retry"
    assert coding_calls[0]["plan_steps"] == ["src/client.py"]
    assert coding_calls[1]["review_feedback"].startswith("Missing null check")
    worktree = runs_root() / key / "web"
    assert git(worktree, "branch", "--show-current").strip() == f"feature/{key}-retry", "branch created on first pass"
    assert not (runs_root() / key / "api").exists(), "only the selected repo gets a worktree"
    assert status["iteration"] == 2 and status["phase_index"] == 0

    # 6. Continue -> phase 2 (tests) coded with a fresh iteration counter, approved first time.
    fakes["llm"].review_script = [True]
    fakes["llm"].review_calls = 0
    await service.submit_decision(key, {"action": "continue"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"
    assert status["pending"]["type"] == "final_review"
    assert status["pending"]["pr_enabled"] is True
    assert fakes["runner"].calls[-1]["plan_steps"] == ["tests/test_client.py"]
    assert status["phase_index"] == 1 and status["iteration"] == 1
    assert status["diffs"]["web"].startswith("diff --git")

    # 7. Create the PR with a human-supplied title.
    await service.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry transient errors"})
    status = await wait_paused(service, key)
    assert status["status"] == "done"
    assert status["pr_urls"] == {"web": "https://bitbucket.invalid/web-repo/pr/7"}
    assert [(p["title"], p["source"], p["dest"], p["slug"]) for p in fakes["prs"]] == [
        ("TEST: retry transient errors", f"feature/{key}-retry", "develop", "web-repo")
    ]
    assert f"feature/{key}-retry" in remote_branches("web"), "the run branch was pushed"
    pushed = git(base_clone("web"), "log", "-1", "--format=%s", f"origin/feature/{key}-retry").strip()
    assert pushed == "TEST-1: implement"
    assert "Part of a multi-repository change" not in fakes["prs"][0]["description"], "single repo: no sibling note"

    runs = await service.list_runs()
    assert any(r["issue_key"] == key and r["status"] == "done" for r in runs)


async def test_revise_requirements_and_cancel(service, fakes):
    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)

    await service.submit_decision(key, {"action": "revise", "notes": "Mention the timeout"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_requirements"
    assert fakes["llm"].framing_calls == 2
    assert "Mention the timeout" in fakes["llm"].last_prompts["framing"]

    await service.submit_decision(key, {"action": "cancel"})
    status = await wait_paused(service, key)
    assert status["status"] == "cancelled"


async def test_plan_reject_cancels(service, fakes):
    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_plan"
    await service.submit_decision(key, {"action": "reject"})
    status = await wait_paused(service, key)
    assert status["status"] == "cancelled"


async def test_all_at_once_mode_and_finish_without_pr(service, fakes):
    key = _key()
    fakes["llm"].review_script = [True]
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"
    assert fakes["runner"].calls[-1]["plan_steps"] == ["src/client.py", "tests/test_client.py"]

    await service.submit_decision(key, {"action": "finish"})
    status = await wait_paused(service, key)
    assert status["status"] == "done" and status["pr_urls"] == {}
    assert fakes["prs"] == []


async def test_review_exhaustion_fails(service, fakes):
    key = _key()
    fakes["llm"].review_script = [False, False, False]
    fakes["runner"].phases = False
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    status = await wait_paused(service, key)
    assert status["status"] == "failed"
    assert len([c for c in fakes["runner"].calls if c["execute"]]) == 2


async def test_wrong_decision_for_gate_is_rejected(service, fakes):
    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    with pytest.raises(ValueError):
        await service.submit_decision(key, {"action": "create_pr", "pr_title": "x"})
    with pytest.raises(ValueError):
        await service.submit_decision(key, {"action": "revise"})  # notes required


async def test_duplicate_start_does_not_restart_paused_run(service, fakes):
    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    assert fakes["llm"].framing_calls == 1
    await service.start_run(key)
    status = await wait_paused(service, key)
    assert status["status"] == "pending_requirements"
    assert fakes["llm"].framing_calls == 1


async def test_multi_repo_run_plans_across_repos_and_opens_one_pr_each(service, fakes):
    """Selecting two repos attaches both to the agents and delivers a PR per repo."""
    key = _key()
    fakes["llm"].review_script = [True]
    await service.start_run(key, repos=["api", "web"])

    status = await wait_paused(service, key)
    assert status["repos"] == ["web", "api"], "repos.json order decides the primary, not click order"

    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)

    # Both the planner and the coder get every selected repo as a root, primary first.
    plan_call = next(c for c in fakes["runner"].calls if not c["execute"])
    assert plan_call["repo_roots"] == ["web", "api"]
    assert plan_call["repo_cwd"].endswith("web")

    # Plan steps say which repo they belong to, and the gate shows repo-qualified paths.
    plan_steps = (await service.get_status(key))["plan_result"]["plan_steps"]
    assert [(s["repo"], s["file"]) for s in plan_steps] == [
        ("web", "src/client.py"),
        ("api", "tests/test_client.py"),
    ]

    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"
    assert status["pending"]["files_changed"] == ["web/src/client.py", "api/tests/test_client.py"]
    assert sorted(status["diffs"]) == ["api", "web"], "one working-tree diff per repo"
    assert "src/client.py" in status["diffs"]["web"] and "tests/test_client.py" in status["diffs"]["api"]
    for name in ("web", "api"):
        assert git(runs_root() / key / name, "branch", "--show-current").strip() == f"feature/{key}-retry"

    # The reviewer sees both diffs, labelled, so cross-repo references resolve.
    review_prompt = fakes["llm"].last_prompts["review"]
    assert "Diff in repo 'web'" in review_prompt and "Diff in repo 'api'" in review_prompt

    await service.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry transient errors"})
    status = await wait_paused(service, key)
    assert status["status"] == "done"
    assert status["pr_urls"] == {
        "web": "https://bitbucket.invalid/web-repo/pr/7",
        "api": "https://bitbucket.invalid/api-repo/pr/7",
    }
    # Each repo's PR targets its own configured branch.
    assert [(p["slug"], p["dest"]) for p in fakes["prs"]] == [("web-repo", "develop"), ("api-repo", "main")]
    assert all("Part of a multi-repository change" in p["description"] for p in fakes["prs"])
    assert f"feature/{key}-retry" in remote_branches("api")


async def test_unknown_repo_is_rejected(service):
    with pytest.raises(ValueError, match="Unknown repo"):
        await service.start_run(_key(), repos=["nope"])


async def _to_final_gate(service, fakes, key: str) -> dict:
    fakes["llm"].review_script = [True]
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"
    return status


async def test_two_issues_on_one_repo_do_not_see_each_others_edits(service, fakes):
    first, second = _key(), _key()
    a = await _to_final_gate(service, fakes, first)
    b = await _to_final_gate(service, fakes, second)

    assert first in a["diffs"]["web"] and second not in a["diffs"]["web"]
    assert second in b["diffs"]["web"] and first not in b["diffs"]["web"]
    assert git(base_clone("web"), "status", "--porcelain") == "", "the base clone is never edited"


@pytest.mark.parametrize("ending", ["cancel", "finish", "create_pr"])
async def test_ended_run_leaves_no_worktree_or_branch(service, fakes, ending):
    key = _key()
    if ending == "cancel":
        await service.start_run(key)
        await wait_paused(service, key)
        await service.submit_decision(key, {"action": "approve"})
        status = await wait_paused(service, key)
        assert status["status"] == "pending_plan" and (runs_root() / key / "web").is_dir()
        await service.submit_decision(key, {"action": "reject"})
    else:
        await _to_final_gate(service, fakes, key)
        assert (runs_root() / key / "web").is_dir()
        await service.submit_decision(key, {"action": ending, "pr_title": "TEST: retry"})
    status = await wait_paused(service, key)
    assert status["status"] in {"cancelled", "done"}

    assert not (runs_root() / key).exists()
    assert key not in git(base_clone("web"), "worktree", "list")
    assert f"feature/{key}-retry" not in git(base_clone("web"), "branch", "--list")
    assert key not in git(base_clone("web"), "for-each-ref", "refs/pi-jira"), "snapshot refs go with the run"
    assert (f"feature/{key}-retry" in remote_branches("web")) is (ending == "create_pr")


async def test_missing_worktree_stops_the_run_instead_of_starting_empty(service, fakes):
    import shutil

    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    shutil.rmtree(runs_root() / key)

    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "stuck_error"
    assert "worktree" in status["error"] and "missing" in status["error"]


async def test_runs_beyond_the_cap_wait_for_a_slot(fakes, monkeypatch):
    import asyncio

    from pi_jira_agent.config import settings
    from pi_jira_agent.graph.slots import WAITING_LABEL
    from pi_jira_agent.service import AutomationService

    monkeypatch.setattr(settings, "max_concurrent_runs", 1)
    svc = AutomationService()
    await svc.start()
    try:
        fakes["runner"].gate = asyncio.Event()
        keys = [_key(), _key()]
        for key in keys:
            await svc.start_run(key)
            await wait_paused(svc, key)
            await svc.submit_decision(key, {"action": "approve"})

        for _ in range(500):
            labels = [(await svc.get_status(key))["node_label"] for key in keys]
            if WAITING_LABEL in labels and len(fakes["runner"].calls) == 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError(f"second run never waited for a slot: {labels}")

        fakes["runner"].gate.set()
        for key in keys:
            assert (await wait_paused(svc, key))["status"] == "pending_plan"
        assert len(fakes["runner"].calls) == 2
    finally:
        await svc.stop()


async def test_new_file_reaches_the_review_prompt_and_the_final_gate(service, fakes):
    key = _key()
    status = await _to_final_gate(service, fakes, key)

    diff = status["pending"]["diffs"]["web"]
    assert "new file mode" in diff and "b/tests/test_client.py" in diff
    assert f"+# {key} attempt" in diff.split("b/tests/test_client.py")[-1]
    assert "b/tests/test_client.py" in fakes["llm"].last_prompts["review"]
    # Taking the snapshot must not stage anything in the run's worktree.
    assert git(runs_root() / key / "web", "diff", "--cached", "--name-only") == ""


async def test_oversized_diff_is_reviewed_partially_and_the_gate_says_so(fakes, monkeypatch):
    from pi_jira_agent.config import settings
    from pi_jira_agent.service import AutomationService

    monkeypatch.setattr(settings, "review_max_diff_chars", 250)
    svc = AutomationService()
    await svc.start()
    try:
        key = _key()
        status = await _to_final_gate(svc, fakes, key)
        omitted = status["pending"]["review_omitted_files"]
        assert len(omitted) == 1, "one of the two changed files no longer fits"
        prompt = fakes["llm"].last_prompts["review"]
        assert "left out" in prompt and omitted[0] in prompt
        assert f"b/{omitted[0]}" not in prompt
        assert f"b/{omitted[0]}" in status["pending"]["diffs"]["web"], "the human still sees the whole diff"
    finally:
        await svc.stop()


async def test_phase_review_sees_only_that_phases_changes(service, fakes):
    key = _key()
    fakes["llm"].review_script = [True]
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve", "mode": "phased"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_phase"
    assert "b/src/client.py" in fakes["llm"].last_prompts["review"]
    assert "b/src/client.py" in status["pending"]["phase_diff"]["web"]

    await service.submit_decision(key, {"action": "continue"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"

    # Phase 2 only created the test file; phase 1's edit was reviewed already.
    prompt = fakes["llm"].last_prompts["review"]
    assert "b/tests/test_client.py" in prompt and "b/src/client.py" not in prompt

    first, second = status["phase_diffs"]
    assert "b/src/client.py" in first["web"] and "b/tests/test_client.py" not in first["web"]
    assert "b/tests/test_client.py" in second["web"] and "b/src/client.py" not in second["web"]
    # The human's final diff is still the whole change.
    assert "b/src/client.py" in status["diffs"]["web"] and "b/tests/test_client.py" in status["diffs"]["web"]


async def test_failed_review_leaves_a_clean_start_for_the_next_run(service, fakes):
    """A run that fails review must not leak its edits into the next run on the same repository."""
    failed = _key()
    fakes["llm"].review_script = [False, False, False]
    fakes["runner"].phases = False
    await service.start_run(failed)
    await wait_paused(service, failed)
    await service.submit_decision(failed, {"action": "approve"})
    await wait_paused(service, failed)
    await service.submit_decision(failed, {"action": "approve"})
    status = await wait_paused(service, failed)
    assert status["status"] == "failed"

    assert not (runs_root() / failed).exists()
    assert failed not in git(base_clone("web"), "worktree", "list")
    assert f"feature/{failed}-retry" not in git(base_clone("web"), "branch", "--list")
    assert git(base_clone("web"), "status", "--porcelain") == ""

    # Running the same issue again starts from the target branch, not from the failed attempt.
    fakes["llm"].review_calls = 0
    again = await _to_final_gate(service, fakes, failed)
    attempts = [line for line in again["diffs"]["web"].splitlines() if line.startswith(f"+# {failed} attempt")]
    assert len(attempts) == 2, "one line per file from this run only; nothing left from the failed attempts"


async def _delivery_service(monkeypatch):
    """A service that comments on Jira and transitions the issue after the pull requests."""
    from pi_jira_agent.config import settings
    from pi_jira_agent.service import AutomationService

    monkeypatch.setattr(settings, "jira_comments_enabled", True)
    monkeypatch.setattr(settings, "jira_transition_done_id", "31")
    svc = AutomationService()
    await svc.start()
    return svc


async def _two_repos_to_final_gate(svc, fakes, key: str) -> None:
    fakes["llm"].review_script = [True]
    await svc.start_run(key, repos=["web", "api"])
    await wait_paused(svc, key)
    await svc.submit_decision(key, {"action": "approve"})
    await wait_paused(svc, key)
    await svc.submit_decision(key, {"action": "approve", "mode": "all"})
    assert (await wait_paused(svc, key))["status"] == "pending_final"


async def test_pr_step_can_be_retried_without_duplicates(fakes, monkeypatch):
    svc = await _delivery_service(monkeypatch)
    try:
        key = _key()
        await _two_repos_to_final_gate(svc, fakes, key)

        # The first repo gets its pull request; the second one fails.
        fakes["fail_once"].add("api-repo")
        await svc.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry"})
        status = await wait_paused(svc, key)
        assert status["status"] == "stuck_error" and "api-repo" in status["error"]
        assert [p["slug"] for p in fakes["prs"]] == ["web-repo"]

        await svc.retry(key)
        status = await wait_paused(svc, key)
        assert status["status"] == "done"
        assert sorted(p["slug"] for p in fakes["prs"]) == ["api-repo", "web-repo"], "exactly one PR per repository"
        assert sorted(status["pr_urls"]) == ["api", "web"]
        announcements = [c for c in fakes["comments"][key] if "pull requests" in c]
        assert len(announcements) == 1 and all(url in announcements[0] for url in status["pr_urls"].values())
        assert fakes["transitions"] == [f"{key}:31"]
    finally:
        await svc.stop()


async def test_failed_jira_transition_does_not_repeat_the_pr_or_the_comment(fakes, monkeypatch):
    svc = await _delivery_service(monkeypatch)
    try:
        key = _key()
        await _two_repos_to_final_gate(svc, fakes, key)

        fakes["fail_once"].add("transition")
        await svc.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry"})
        status = await wait_paused(svc, key)
        assert status["status"] == "stuck_error" and status["stuck_on"] == ["announce_node"]
        assert len(fakes["prs"]) == 2 and sorted(status["pr_urls"]) == ["api", "web"], "PR URLs are already saved"

        await svc.retry(key)
        status = await wait_paused(svc, key)
        assert status["status"] == "done"
        assert len(fakes["prs"]) == 2
        assert len([c for c in fakes["comments"][key] if "pull requests" in c]) == 1
        assert fakes["transitions"] == [f"{key}:31"]
    finally:
        await svc.stop()


async def test_slow_git_push_does_not_block_the_api(service, fakes, monkeypatch):
    import asyncio
    import threading
    import time

    from pi_jira_agent.git_client import GitBranchClient

    pushing, pushed = threading.Event(), threading.Event()
    real_push = GitBranchClient.push_branch

    def slow_push(self, branch):
        pushing.set()
        time.sleep(1.0)
        real_push(self, branch)
        pushed.set()

    monkeypatch.setattr(GitBranchClient, "push_branch", slow_push)
    key = _key()
    await _to_final_gate(service, fakes, key)
    await service.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry"})

    while not pushing.is_set():
        await asyncio.sleep(0.01)
    started = time.monotonic()
    runs = await service.list_runs()
    assert not pushed.is_set(), "the event loop only got control back after the push had finished"
    assert time.monotonic() - started < 0.5 and any(r["issue_key"] == key for r in runs)

    assert (await wait_paused(service, key))["status"] == "done"


async def test_restarting_a_finished_issue_starts_from_clean_state(service, fakes):
    key = _key()
    await _to_final_gate(service, fakes, key)
    await service.submit_decision(key, {"action": "create_pr", "pr_title": "TEST: retry"})
    finished = await wait_paused(service, key)
    assert finished["status"] == "done" and finished["plan_result"] and finished["pr_urls"]

    await service.start_run(key)
    status = await wait_paused(service, key)
    assert status["status"] == "pending_requirements"
    leftovers = {
        name: status[name]
        for name in ("plan_result", "scope_check", "code_result", "diffs", "phase_diffs", "pr_urls", "pr_title")
        if status[name]
    }
    assert leftovers == {}, "nothing from the finished run may show in the new one"
    assert status["review_approved"] is None and not status["review_feedback"]
    assert status["iteration"] == 0 and status["phase_index"] == 0 and status["retry_count"] == 0


async def test_two_concurrent_decisions_resume_the_gate_once(service, fakes):
    import asyncio

    from pi_jira_agent.service import ConflictError

    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)

    results = await asyncio.gather(
        service.submit_decision(key, {"action": "approve"}),
        service.submit_decision(key, {"action": "approve"}),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConflictError) for r in results) == 1, results
    assert sum(isinstance(r, dict) for r in results) == 1

    status = await wait_paused(service, key)
    assert status["status"] == "pending_plan"
    assert fakes["llm"].scope_calls == 0 and len(fakes["runner"].calls) == 1, "planning ran once"


async def test_decision_for_an_earlier_gate_is_refused(service, fakes):
    from pi_jira_agent.service import ConflictError

    key = _key()
    await service.start_run(key)
    first = (await wait_paused(service, key))["pending"]["gate_id"]

    # Revise brings the same kind of gate back: only its id tells the two apart.
    await service.submit_decision(key, {"action": "revise", "notes": "Mention the timeout", "gate_id": first})
    status = await wait_paused(service, key)
    second = status["pending"]["gate_id"]
    assert status["pending"]["type"] == "requirements_approval" and first and second and first != second

    # A double-click or a redelivered request still carries the first gate's id.
    with pytest.raises(ConflictError, match="earlier gate"):
        await service.submit_decision(key, {"action": "approve", "gate_id": first})
    assert (await service.get_status(key))["status"] == "pending_requirements"

    await service.submit_decision(key, {"action": "approve", "gate_id": second})
    assert (await wait_paused(service, key))["status"] == "pending_plan"


async def test_retry_is_refused_while_the_run_is_running(service, fakes):
    import asyncio

    from pi_jira_agent.service import ConflictError

    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    fakes["runner"].gate = asyncio.Event()
    await service.submit_decision(key, {"action": "approve"})
    try:
        with pytest.raises(ConflictError):
            await service.retry(key)
    finally:
        fakes["runner"].gate.set()
    assert (await wait_paused(service, key))["status"] == "pending_plan"
    assert len(fakes["runner"].calls) == 1


async def _restart(old):
    """Stop a service the way a deploy does and bring a new one up on the same storage."""
    from pi_jira_agent.service import AutomationService

    await old.stop()
    new = AutomationService()
    await new.start()
    return new


async def _wait_for_execute_calls(fakes, count: int) -> None:
    import asyncio

    for _ in range(500):
        if len([c for c in fakes["runner"].calls if c["execute"]]) >= count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the coding agent was not called {count} time(s)")


async def test_interrupted_run_resumes_once_after_a_restart(fakes):
    import asyncio

    from pi_jira_agent.service import AutomationService

    svc = AutomationService()
    await svc.start()
    try:
        key = _key()
        fakes["llm"].review_script = [True]
        fakes["runner"].phases = False
        await svc.start_run(key)
        await wait_paused(svc, key)
        await svc.submit_decision(key, {"action": "approve"})
        await wait_paused(svc, key)

        # The service goes down while the coding agent is mid-session, its edits on disk.
        fakes["runner"].execute_gate = asyncio.Event()
        await svc.submit_decision(key, {"action": "approve", "mode": "all"})
        await _wait_for_execute_calls(fakes, 1)
        svc = await _restart(svc)

        # Restart 1: the run is picked up without anyone asking, and goes down again mid-session.
        await _wait_for_execute_calls(fakes, 2)
        assert (await svc.get_status(key))["running"] is True
        svc = await _restart(svc)

        # Restart 2: it already had its one automatic resume, so it waits for a person.
        status = await svc.get_status(key)
        assert status["running"] is False and status["status"] == "stuck_error"
        assert "interrupted" in status["error"] and status["stuck_on"] == ["coding_agent"]
        assert len([c for c in fakes["runner"].calls if c["execute"]]) == 2

        fakes["runner"].execute_gate = None
        await svc.retry(key)
        status = await wait_paused(svc, key)
        assert status["status"] == "pending_final"
        attempts = [line for line in status["diffs"]["web"].splitlines() if line.startswith(f"+# {key} attempt")]
        assert len(attempts) == 2, "one line per file: the two interrupted sessions' edits were discarded"
    finally:
        await svc.stop()


async def test_run_waiting_at_a_gate_is_left_alone_by_a_restart(fakes):
    from pi_jira_agent.service import AutomationService

    svc = AutomationService()
    await svc.start()
    try:
        key = _key()
        await svc.start_run(key)
        before = await wait_paused(svc, key)
        svc = await _restart(svc)
        after = await svc.get_status(key)
        assert after["running"] is False and after["status"] == "pending_requirements"
        assert after["pending"]["gate_id"] == before["pending"]["gate_id"]
        assert fakes["llm"].framing_calls == 1
    finally:
        await svc.stop()


async def test_run_interrupted_in_its_first_node_resumes_after_a_restart(fakes, monkeypatch):
    import asyncio

    from pi_jira_agent import jira_client
    from pi_jira_agent.service import AutomationService

    real_get_issue = jira_client.JiraClient.get_issue
    hold = asyncio.Event()
    entered = asyncio.Event()

    async def held_get_issue(self, key):
        entered.set()
        await hold.wait()
        return await real_get_issue(self, key)

    monkeypatch.setattr(jira_client.JiraClient, "get_issue", held_get_issue)
    svc = AutomationService()
    await svc.start()
    try:
        key = _key()
        await svc.start_run(key)
        await entered.wait()
        hold.set()  # the restarted service must not be held as well
        svc = await _restart(svc)
        status = await wait_paused(svc, key)
        assert status["status"] == "pending_requirements" and status["auto_resumes"] == 1
    finally:
        await svc.stop()


async def test_run_starts_from_the_last_fetched_commit_when_the_remote_is_unreachable(service, fakes):
    """An expired token or no network must not stop planning and coding; the run says it may be stale."""
    from pi_jira_agent.graph import progress

    clone = base_clone("web")
    real_url = git(clone, "remote", "get-url", "origin").strip()
    git(clone, "remote", "set-url", "origin", str(runs_root() / "no-such-remote.git"))
    try:
        key = _key()
        status = await _to_final_gate(service, fakes, key)
        assert status["base_shas"]["web"] == git(clone, "rev-parse", "origin/develop").strip()
        warnings = [e["text"] for e in progress.events(key, limit=400) if e.get("ev") == "warning"]
        assert len(warnings) == 1 and "Could not fetch web" in warnings[0] and "may be behind" in warnings[0]
    finally:
        git(clone, "remote", "set-url", "origin", real_url)
