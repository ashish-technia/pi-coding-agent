import pytest

from pi_jira_agent.channels.jira_comments import render_pending
from pi_jira_agent.graph.decisions import command_help, parse_comment_command, parse_decision


def test_parse_decision_validates_per_gate():
    d = parse_decision("plan_approval", {"action": "approve", "mode": "phased"})
    assert d.model_dump()["mode"] == "phased"
    with pytest.raises(ValueError):
        parse_decision("plan_approval", {"action": "continue"})
    with pytest.raises(ValueError):
        parse_decision("final_review", {"action": "create_pr", "pr_title": ""})
    with pytest.raises(ValueError):
        parse_decision("nope", {"action": "approve"})


@pytest.mark.parametrize(
    "pending, body, expected",
    [
        ("requirements_approval", "/approve", {"action": "approve"}),
        (
            "requirements_approval",
            "/revise please mention timeouts",
            {"action": "revise", "notes": "please mention timeouts"},
        ),
        ("requirements_approval", "/cancel", {"action": "cancel"}),
        ("plan_approval", "/approve phased", {"action": "approve", "mode": "phased"}),
        ("plan_approval", "/Approve", {"action": "approve", "mode": "all"}),
        ("plan_approval", "/revise split the migration", {"action": "refine", "notes": "split the migration"}),
        ("plan_approval", "/reject", {"action": "reject"}),
        ("phase_gate", "/continue", {"action": "continue"}),
        ("phase_gate", "/stop", {"action": "stop"}),
        (
            "final_review",
            "/pr WAAS-1: retry transient errors",
            {"action": "create_pr", "pr_title": "WAAS-1: retry transient errors"},
        ),
        ("final_review", "/finish", {"action": "finish"}),
        ("final_review", "looks great, thanks!", None),
        ("plan_approval", "/pr not valid here", None),
    ],
)
def test_parse_comment_command(pending, body, expected):
    assert parse_comment_command(pending, body) == expected


def test_command_help_and_render_pending_mention_every_command():
    text = render_pending(
        {
            "type": "plan_approval",
            "plan": {
                "analysis": "a",
                "plan_steps": [{"action": "modify", "file": "x.py", "change": "c"}],
                "verification": ["pytest"],
                "phases": [{"name": "Core", "step_indexes": [0]}],
            },
        }
    )
    for cmd in ("/approve", "/approve phased", "/revise", "/reject"):
        assert cmd in text
    assert "/pr" in command_help("final_review")
