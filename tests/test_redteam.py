"""Hostile Jira text (R-15): it reaches a prompt only as marked data.

The cases live in ``tests/redteam/injections.json`` and are shared with the runner's own
red-team test (``node/tests/redteam.test.mjs``), which plays a model that obeys them.
"""

import json
import uuid
from pathlib import Path

import pytest

from pi_jira_agent import untrusted
from pi_jira_agent.graph.nodes.review_agent import _build_review_prompt
from pi_jira_agent.models import JiraComment, JiraIssue
from tests.conftest import wait_paused

CASES = json.loads((Path(__file__).parent / "redteam" / "injections.json").read_text(encoding="utf-8"))["cases"]


def _issue(case: dict, key: str = "RED-1") -> JiraIssue:
    text = case["text"]
    return JiraIssue(
        key=key,
        project_key=key.split("-")[0],
        summary=text if case["where"] == "summary" else "Add retry to client",
        description=text if case["where"] == "description" else "Client should retry transient errors.",
        reporter="Mallory",
        comments=[JiraComment(author="Mallory", created="2026-10-01", body=text)] if case["where"] == "comment" else [],
    )


def _inside_markers(prompt: str) -> str:
    """The single marked block of ``prompt``; fails when the markers are missing or repeated."""
    assert prompt.count(untrusted.BEGIN) == 1, "exactly one opening marker"
    assert prompt.count(untrusted.END) == 1, "exactly one closing marker"
    start, end = prompt.index(untrusted.BEGIN), prompt.index(untrusted.END)
    assert prompt.index(untrusted.NOTICE) < start < end
    return prompt[start:end]


def test_the_set_is_large_enough_and_every_case_is_well_formed():
    assert len(CASES) >= 15
    assert len({c["id"] for c in CASES}) == len(CASES)
    for case in CASES:
        assert case["where"] in {"summary", "description", "comment"}
        if "attempt" in case:
            assert case["expect"] in {"blocked", "allowed"}
            assert case["expect"] == "blocked" or case.get("gap"), "a known gap must say what closes it"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_hostile_text_stays_inside_the_marked_block(case):
    prompt = _issue(case).as_context()
    inside = _inside_markers(prompt)
    # The words arrive (the analyst must be able to read the issue), with the marker word defused.
    assert untrusted.neutralise(case["text"]) in inside
    assert prompt.endswith(untrusted.END), "nothing of the issue follows the closing marker"


@pytest.mark.parametrize("case", [c for c in CASES if c["where"] != "comment"], ids=lambda c: c["id"])
def test_the_phase_reviewer_gets_issue_text_marked_too(case):
    prompt = _build_review_prompt(
        _issue(case),
        {"web": "diff --git a/a b/a\n"},
        requirements=None,
        plan=None,
        rules="",
        iteration=1,
        previous_feedback="",
    )
    text = untrusted.neutralise(case["text"])
    assert text in prompt
    # Summary and description are each their own block; the hostile text is inside one of them.
    blocks = [chunk.split(untrusted.END)[0] for chunk in prompt.split(untrusted.BEGIN)[1:]]
    assert any(text in block for block in blocks)
    assert prompt.count(untrusted.BEGIN) == prompt.count(untrusted.END) == 2


@pytest.mark.asyncio
async def test_the_analyst_is_sent_the_issue_as_marked_data(service, fakes):
    case = next(c for c in CASES if c["id"] == "read-env-file")
    key = f"RED-{uuid.uuid4().hex[:6].upper()}"
    await service.start_run(key, inline_issue=_issue(case, key))
    await wait_paused(service, key)
    sent = fakes["llm"].last_prompts["framing"]
    assert case["text"] in _inside_markers(sent)
