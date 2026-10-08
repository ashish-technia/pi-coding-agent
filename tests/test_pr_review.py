"""The whole-change PR review before the final gate (R-56) and the fix loop (R-57)."""

import uuid

import pytest

from pi_jira_agent.graph.decisions import parse_comment_command, parse_decision, resolve_fix
from tests.conftest import runs_root, wait_paused

pytestmark = pytest.mark.asyncio

MUST = {
    "severity": "must",
    "category": "bug",
    "claim": "Retries forever on a 503",
    "suggestion": "Stop after 3 attempts",
    "file": "src/client.py",
    "line": 12,
}
SHOULD = {"severity": "should", "category": "tests", "claim": "No test for the give-up path", "file": "", "line": None}


def _key() -> str:
    return f"TEST-{uuid.uuid4().hex[:6].upper()}"


async def _to_final(service, key, **start) -> dict:
    """Drive a run to the final gate, approving everything on the way."""
    await service.start_run(key, **start)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final", status.get("error")
    return status


async def test_final_gate_shows_the_review_of_the_whole_change(service, fakes):
    fakes["runner"].review_script = [[MUST, SHOULD]]
    key = _key()
    status = await _to_final(service, key)

    review = status["pending"]["pr_review"]
    assert review["enabled"] and not review["skipped"]
    assert [(f["number"], f["severity"]) for f in review["findings"]] == [(1, "must"), (2, "should")]
    assert review["fix_available"] and review["fix_rounds"] == 0 and review["max_fix_rounds"] == 2
    assert status["pr_review"]["summary"] == "2 finding(s) across 1 repo(s)."

    call = fakes["runner"].review_calls[0]
    assert call["key"] == key
    assert call["requirements"] == "Add retry to client", "the reviewer judges against the approved requirement"
    assert call["previous"] == []
    assert any(s["stage"] == "pr_review" for s in status["usage"]["by_stage"])


async def test_run_started_without_pr_review_skips_it(service, fakes):
    fakes["runner"].review_script = [[MUST]]
    key = _key()
    status = await _to_final(service, key, pr_review=False)

    assert fakes["runner"].review_calls == []
    review = status["pending"]["pr_review"]
    assert review == {**review, "enabled": False, "findings": [], "fix_available": False}
    with pytest.raises(ValueError, match="no PR review"):
        await service.submit_decision(key, {"action": "fix", "findings": [1]})


async def test_fix_sends_chosen_findings_back_and_reviews_again(service, fakes):
    runner, llm = fakes["runner"], fakes["llm"]
    runner.review_script = [[MUST, SHOULD], [SHOULD]]
    key = _key()
    await _to_final(service, key)
    coding_calls, phase_reviews = len(runner.calls), llm.review_calls

    # No numbers: every `must` finding. The notes travel with them.
    await service.submit_decision(key, {"action": "fix", "notes": "Keep the public API as it is."})
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"

    fix_pass = runner.calls[-1]
    assert len(runner.calls) == coding_calls + 1 and fix_pass["execute"]
    assert "Retries forever on a 503" in fix_pass["review_feedback"]
    assert "Keep the public API as it is." in fix_pass["review_feedback"]
    assert "No test for the give-up path" not in fix_pass["review_feedback"], "only the chosen finding goes back"
    assert fix_pass["plan_steps"] == ["src/client.py", "tests/test_client.py"], "a fix pass works on the whole plan"
    assert llm.review_calls == phase_reviews, "a fix pass is not judged by the phase reviewer"

    # The re-review was told what to check, and the gate shows what it confirmed.
    assert runner.review_calls[1]["previous"] == [1]
    assert runner.review_calls[1]["notes"] == "Keep the public API as it is."
    review = status["pending"]["pr_review"]
    assert review["resolved"] == [1]
    assert [f["severity"] for f in review["findings"]] == ["should"]
    assert review["fix_rounds"] == 1 and review["fix_available"]

    # The decision log keeps what was shown and what the human did with it.
    entry = status["decision_log"][-1]
    assert entry["action"] == "fix"
    assert entry["review"] == {
        "shown": [
            {"number": 1, "severity": "must", "category": "bug"},
            {"number": 2, "severity": "should", "category": "tests"},
        ],
        "fix": [1],
        "left": [2],
    }


async def test_fix_rounds_are_capped(service, fakes):
    fakes["runner"].review_script = [[MUST], [MUST], [MUST]]
    key = _key()
    await _to_final(service, key)
    for _ in range(2):
        await service.submit_decision(key, {"action": "fix", "findings": [1]})
        status = await wait_paused(service, key)
        assert status["status"] == "pending_final"

    review = status["pending"]["pr_review"]
    assert review["fix_rounds"] == 2 and not review["fix_available"]
    assert review["findings"], "the findings are still shown"
    with pytest.raises(ValueError, match="already sent back 2 time"):
        await service.submit_decision(key, {"action": "fix", "findings": [1]})
    # The human can still end the run either way.
    await service.submit_decision(key, {"action": "finish"})
    assert (await wait_paused(service, key))["status"] == "done"


async def test_unfixed_must_findings_go_into_the_pr_description(service, fakes):
    fakes["runner"].review_script = [[MUST, SHOULD]]
    key = _key()
    await _to_final(service, key)
    await service.submit_decision(key, {"action": "create_pr", "pr_title": "Add retry"})
    status = await wait_paused(service, key)
    assert status["status"] == "done"

    description = fakes["prs"][0]["description"]
    assert "## Known review findings" in description
    assert "`src/client.py:12` Retries forever on a 503" in description
    assert "No test for the give-up path" not in description, "only `must` findings are listed"
    assert status["decision_log"][-1]["review"]["left"] == [1, 2]


async def test_failed_review_can_be_retried_or_skipped(service, fakes):
    fakes["runner"].review_error = "model is down"
    key = _key()
    await service.start_run(key)
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve"})
    await wait_paused(service, key)
    await service.submit_decision(key, {"action": "approve", "mode": "all"})
    status = await wait_paused(service, key)
    assert status["status"] == "stuck_error" and status["stuck_on"] == ["pr_review"]
    assert "model is down" in status["error"]
    assert runs_root().joinpath(key).exists()

    await service.retry(key, skip_pr_review=True)
    status = await wait_paused(service, key)
    assert status["status"] == "pending_final"
    review = status["pending"]["pr_review"]
    assert review["enabled"] and review["skipped"] and not review["fix_available"]
    assert status["pr_review_skipped"]
    with pytest.raises(ValueError, match="did not run"):
        await service.submit_decision(key, {"action": "fix"})


async def test_skip_is_refused_when_the_run_is_not_stuck_on_the_review(service, fakes):
    key = _key()
    await _to_final(service, key)
    with pytest.raises(ValueError, match="not stuck on the PR review"):
        await service.retry(key, skip_pr_review=True)


async def test_review_covers_every_repository_of_the_run(service, fakes):
    key = _key()
    await _to_final(service, key, repos=["web", "api"])
    assert fakes["runner"].review_calls[0]["repos"] == ["api", "web"]


def _review(**overrides) -> dict:
    return {
        "enabled": True,
        "fix_available": True,
        "fix_rounds": 0,
        "max_fix_rounds": 2,
        "findings": [{"number": 1, "severity": "must"}, {"number": 2, "severity": "should"}],
        **overrides,
    }


async def test_fix_defaults_to_must_findings_and_rejects_unknown_numbers():
    assert resolve_fix({"action": "fix", "findings": [], "notes": ""}, _review())["findings"] == [1]
    assert resolve_fix({"action": "fix", "findings": [2, 2, 1], "notes": " x "}, _review()) == {
        "action": "fix",
        "findings": [2, 1],
        "notes": "x",
    }
    with pytest.raises(ValueError, match="No finding numbered 7"):
        resolve_fix({"action": "fix", "findings": [7]}, _review())
    only_should = _review(findings=[{"number": 1, "severity": "should"}])
    with pytest.raises(ValueError, match="no 'must' finding"):
        resolve_fix({"action": "fix", "findings": []}, only_should)
    # Notes alone are a valid request when there is a review to act on.
    assert resolve_fix({"action": "fix", "findings": [], "notes": "rename it"}, only_should)["findings"] == []


async def test_fix_command_from_a_jira_comment():
    assert parse_comment_command("final_review", "/fix") == {"action": "fix", "findings": [], "notes": ""}
    assert parse_comment_command("final_review", "/fix 1 3") == {"action": "fix", "findings": [1, 3], "notes": ""}
    assert parse_comment_command("final_review", "/fix 1, 3 keep the API\nas it is") == {
        "action": "fix",
        "findings": [1, 3],
        "notes": "keep the API\nas it is",
    }
    assert parse_comment_command("final_review", "/fix rename the helper") == {
        "action": "fix",
        "findings": [],
        "notes": "rename the helper",
    }
    assert parse_comment_command("plan_approval", "/fix 1") is None
    parsed = parse_decision("final_review", {"action": "fix", "findings": [2]})
    assert parsed.model_dump()["findings"] == [2]
