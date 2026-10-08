"""Standalone review of a branch (R-58): no run, no gates, findings stored on their own."""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from pi_jira_agent.config import settings
from tests.conftest import base_clone, git, runs_root

pytestmark = pytest.mark.asyncio

FINDING = {"severity": "must", "category": "bug", "claim": "Swallows the error", "file": "src/client.py", "line": 3}


def _push_branch(tmp_path, repo: str, files: dict[str, str]) -> tuple[str, str, str]:
    """Push a new branch with ``files`` to the repo's origin. Returns (branch, head sha, fork sha)."""
    origin = git(base_clone(repo), "remote", "get-url", "origin").strip()
    work = tmp_path / f"{repo}-{uuid.uuid4().hex[:6]}"
    git(tmp_path, "clone", "-q", origin, str(work))
    fork = git(work, "rev-parse", "HEAD").strip()
    branch = f"feature/review-{uuid.uuid4().hex[:6]}"
    git(work, "checkout", "-q", "-b", branch)
    for rel, content in files.items():
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "change")
    git(work, "push", "-q", "origin", branch)
    return branch, git(work, "rev-parse", "HEAD").strip(), fork


def _same_branch(tmp_path, repo: str, branch: str, files: dict[str, str]) -> None:
    origin = git(base_clone(repo), "remote", "get-url", "origin").strip()
    work = tmp_path / f"{repo}-{uuid.uuid4().hex[:6]}"
    git(tmp_path, "clone", "-q", origin, str(work))
    git(work, "checkout", "-q", "-b", branch)
    for rel, content in files.items():
        (work / rel).write_text(content, encoding="utf-8", newline="\n")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "change")
    git(work, "push", "-q", "origin", branch)


def _service(tmp_path):
    from pi_jira_agent.graph.build import make_jira_client, make_pi_executor, make_workspaces
    from pi_jira_agent.graph.slots import PiSlots
    from pi_jira_agent.reviews import ReviewService, make_review_store

    return ReviewService(
        store=make_review_store(settings.database_url, str(tmp_path / "reviews.db")),
        workspaces=make_workspaces(),
        slots=PiSlots(2),
        reviewer=make_pi_executor("pr_review"),
        jira=make_jira_client(),
        rules="## Must\n- Handle every error",
    )


@pytest.fixture
async def reviews(fakes, tmp_path):
    svc = _service(tmp_path)
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


async def _finished(svc, review_id: str, timeout: float = 15.0) -> dict:
    for _ in range(int(timeout / 0.02)):
        review = await svc.get(review_id)
        if review and not review["running"]:
            return review
        await asyncio.sleep(0.02)
    raise AssertionError("review still running")


def _repos(*names: str):
    return settings.selected_repos(list(names))


async def test_branch_is_reviewed_from_its_fork_point_and_the_findings_are_kept(reviews, fakes, tmp_path):
    branch, head, fork = _push_branch(tmp_path, "web", {"src/client.py": "one\ntwo\nthree\n"})
    fakes["runner"].review_script = [[FINDING]]

    started = await reviews.start_review(repos=_repos("web"), branch=branch, started_by="ana")
    assert started["status"] in {"queued", "running"} and started["id"].startswith("review-")
    review = await _finished(reviews, started["id"])

    assert review["status"] == "done"
    assert review["branch"] == branch and review["repos"] == ["web"] and review["started_by"] == "ana"
    assert review["commits"] == {"web": {"head": head, "base": fork}}, "a review describes fixed commits"
    assert review["result"]["findings"][0]["claim"] == "Swallows the error"

    call = fakes["runner"].review_calls[0]
    assert call["key"] == review["id"]
    assert call["diff"] == {"web": {"base": fork, "tree": head}}, "merge-base to branch head"
    assert call["issue"] is None and call["requirements"] is None
    assert call["rules"] == "## Must\n- Handle every error"
    assert not runs_root().joinpath(review["id"]).exists(), "the checkout is removed when the review ends"

    listed = next(r for r in await reviews.list() if r["id"] == review["id"])
    assert (listed["findings"], listed["must"], listed["status"]) == (1, 1, "done")
    assert "result" not in listed


async def test_review_is_refused_when_the_branch_is_missing_in_a_chosen_repository(reviews, fakes, tmp_path):
    branch, _, _ = _push_branch(tmp_path, "web", {"a.txt": "x\n"})
    before = len(await reviews.list())
    with pytest.raises(ValueError, match="does not exist in api"):
        await reviews.start_review(repos=_repos("web", "api"), branch=branch)
    assert len(await reviews.list()) == before, "nothing is queued"
    assert fakes["runner"].review_calls == []


@pytest.mark.parametrize("branch", ["", "--upload-pack=x", "a..b", "feature/", "has space"])
async def test_bad_branch_names_never_reach_git(reviews, branch):
    with pytest.raises(ValueError, match="not a branch name"):
        await reviews.start_review(repos=_repos("web"), branch=branch)


async def test_review_spans_repositories_and_uses_the_jira_issue_when_given(reviews, fakes, tmp_path):
    branch, _, _ = _push_branch(tmp_path, "web", {"ui.ts": "x\n"})
    _same_branch(tmp_path, "api", branch, {"routes.py": "y\n"})

    started = await reviews.start_review(repos=_repos("web", "api"), branch=branch, issue_key="test-9")
    review = await _finished(reviews, started["id"])

    assert review["status"] == "done" and review["issue_key"] == "TEST-9"
    call = fakes["runner"].review_calls[0]
    assert sorted(call["diff"]) == ["api", "web"]
    assert call["issue"] == "TEST-9", "the raw issue is what the change is judged against"


async def test_failed_review_says_why_and_leaves_no_checkout(reviews, fakes, tmp_path):
    branch, _, _ = _push_branch(tmp_path, "web", {"a.txt": "x\n"})
    fakes["runner"].review_error = "model is down"
    started = await reviews.start_review(repos=_repos("web"), branch=branch)
    review = await _finished(reviews, started["id"])
    assert review["status"] == "failed" and "model is down" in review["error"]
    assert review["result"] is None
    assert not runs_root().joinpath(review["id"]).exists()


async def test_review_cannot_be_deleted_while_it_runs(reviews, fakes, tmp_path):
    from pi_jira_agent.reviews import ReviewConflict

    branch, _, _ = _push_branch(tmp_path, "web", {"a.txt": "x\n"})
    fakes["runner"].review_gate = asyncio.Event()
    started = await reviews.start_review(repos=_repos("web"), branch=branch)
    with pytest.raises(ReviewConflict):
        await reviews.delete(started["id"])
    fakes["runner"].review_gate.set()
    await _finished(reviews, started["id"])

    assert await reviews.delete(started["id"]) is True
    assert await reviews.get(started["id"]) is None
    assert await reviews.delete(started["id"]) is False


async def test_review_cut_short_by_a_restart_is_marked_interrupted(fakes, tmp_path):
    branch, _, _ = _push_branch(tmp_path, "web", {"a.txt": "x\n"})
    fakes["runner"].review_gate = asyncio.Event()
    first = _service(tmp_path)
    await first.start()
    started = await first.start_review(repos=_repos("web"), branch=branch)
    for _ in range(500):
        if fakes["runner"].review_calls:
            break
        await asyncio.sleep(0.02)
    await first.stop()  # the service goes down mid-review

    second = _service(tmp_path)
    await second.start()
    try:
        review = await second.get(started["id"])
        assert review["status"] == "interrupted" and not review["running"]
        assert not runs_root().joinpath(started["id"]).exists()
    finally:
        await second.stop()


async def test_reviews_over_http(reviews, fakes, tmp_path, monkeypatch):
    from pi_jira_agent import main as main_mod

    monkeypatch.setattr(main_mod, "reviews", reviews)
    branch, _, _ = _push_branch(tmp_path, "web", {"a.txt": "x\n"})
    async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as client:
        response = await client.post("/api/reviews", json={"branch": branch, "repos": ["web"]})
        assert response.status_code == 200, response.text
        review_id = response.json()["id"]
        await _finished(reviews, review_id)

        assert (await client.get(f"/api/reviews/{review_id}")).json()["status"] == "done"
        assert any(r["id"] == review_id for r in (await client.get("/api/reviews")).json())
        assert (
            await client.post("/api/reviews", json={"branch": "no-such-branch", "repos": ["web"]})
        ).status_code == 400
        assert (await client.post("/api/reviews", json={"branch": branch, "repos": ["nope"]})).status_code == 400
        assert (await client.delete(f"/api/reviews/{review_id}")).json() == {"deleted": review_id}
        assert (await client.get(f"/api/reviews/{review_id}")).status_code == 404
        assert (await client.delete(f"/api/reviews/{review_id}")).status_code == 404
