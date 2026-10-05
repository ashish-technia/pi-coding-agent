"""HTTP surface: start, status, decisions, config, and Jira ingress (flow 2)."""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(fakes, monkeypatch):
    from pi_jira_agent import main as main_mod
    from pi_jira_agent.channels.jira_comments import JiraCommentChannel
    from pi_jira_agent.graph.build import make_jira_client

    # Fresh service bound to the fakes; enable the Jira comment channel for flow-2 tests.
    svc = main_mod.AutomationService()
    svc.jira_channel = JiraCommentChannel(make_jira_client(), agent_account_id="agent-123")
    monkeypatch.setattr(main_mod, "automation", svc)
    await svc.start()
    try:
        async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as c:
            yield c, svc
    finally:
        await svc.stop()


async def _wait(client, key, timeout=10.0):
    for _ in range(int(timeout / 0.02)):
        r = await client.get(f"/api/runs/{key}")
        data = r.json()
        if not data["running"]:
            return data
        await asyncio.sleep(0.02)
    raise AssertionError("still running")


async def test_config_hides_secrets(client):
    c, _ = client
    r = await c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["stages"]["review"]["model"]
    assert "api_key" not in body["stages"]["review"]
    assert body["delivery"]["pr_creation_enabled"] is True


async def test_ui_flow_over_http(client):
    c, _ = client
    key = f"API-{uuid.uuid4().hex[:5].upper()}"
    r = await c.post("/api/runs", json={"issue_key": key})
    assert r.status_code == 200
    data = await _wait(c, key)
    assert data["pending"]["type"] == "requirements_approval"

    gate_id = data["pending"]["gate_id"]
    r = await c.post(f"/api/runs/{key}/decision", json={"action": "approve"})
    assert r.status_code == 422, "the UI must say which gate it is answering"
    r = await c.post(f"/api/runs/{key}/decision", json={"action": "approve", "gate_id": gate_id})
    assert r.status_code == 200
    data = await _wait(c, key)
    assert data["pending"]["type"] == "plan_approval"

    # The requirements gate's id no longer answers anything.
    r = await c.post(f"/api/runs/{key}/decision", json={"action": "approve", "gate_id": gate_id})
    assert r.status_code == 409 and "earlier gate" in r.json()["detail"]

    r = await c.post(f"/api/runs/{key}/decision", json={"action": "finish", "gate_id": data["pending"]["gate_id"]})
    assert r.status_code == 400
    assert "not valid" in r.json()["detail"]

    runs = (await c.get("/api/runs")).json()
    assert any(run["issue_key"] == key for run in runs)


async def test_jira_ingress_requires_secret_and_drives_run_by_comments(client, fakes):
    c, svc = client
    key = f"JIRA-{uuid.uuid4().hex[:5].upper()}"

    r = await c.post("/webhooks/jira/trigger", json={"issue_key": key})
    assert r.status_code == 401

    r = await c.post("/webhooks/jira/trigger", json={"issue_key": key}, headers={"x-webhook-secret": "test-secret"})
    assert r.status_code == 200 and r.json()["status"] == "started"
    data = await _wait(c, key)
    assert data["channel"] == "jira" and data["pending"]["type"] == "requirements_approval"

    # The agent's own comment is ignored; a human command resumes the run.
    r = await c.post(
        "/webhooks/jira/comment",
        json={"issue_key": key, "comment_body": "/approve", "author_account_id": "agent-123"},
        headers={"x-webhook-secret": "test-secret"},
    )
    assert r.json()["handled"] is False

    approve = {"issue_key": key, "comment_body": "/approve", "author_account_id": "acc-ana", "comment_id": "10001"}
    r = await c.post("/webhooks/jira/comment", json=approve, headers={"x-webhook-secret": "test-secret"})
    assert r.json()["handled"] is True
    data = await _wait(c, key)
    assert data["pending"]["type"] == "plan_approval"

    # Jira Automation delivers the same comment again: it must not approve the plan as well.
    r = await c.post("/webhooks/jira/comment", json=approve, headers={"x-webhook-secret": "test-secret"})
    assert r.json()["handled"] is False and "already" in r.json()["reason"]
    assert (await _wait(c, key))["pending"]["type"] == "plan_approval"

    r = await c.post(
        "/webhooks/jira/comment",
        json={"issue_key": key, "comment_body": "thanks, looks fine", "author_account_id": "acc-ana"},
        headers={"x-webhook-secret": "test-secret"},
    )
    assert r.json()["handled"] is False and "not a command" in r.json()["reason"]


async def _jira_run_at_requirements_gate(c) -> str:
    key = f"JIRA-{uuid.uuid4().hex[:5].upper()}"
    r = await c.post("/webhooks/jira/trigger", json={"issue_key": key}, headers={"x-webhook-secret": "test-secret"})
    assert r.status_code == 200
    assert (await _wait(c, key))["pending"]["type"] == "requirements_approval"
    return key


async def _comment(c, key, body, author, comment_id):
    r = await c.post(
        "/webhooks/jira/comment",
        json={"issue_key": key, "comment_body": body, "author_account_id": author, "comment_id": comment_id},
        headers={"x-webhook-secret": "test-secret"},
    )
    return r.json()


async def test_only_approvers_can_answer_a_gate_from_jira(client, fakes):
    c, _ = client
    key = await _jira_run_at_requirements_gate(c)

    # Someone who is neither the reporter nor the assignee: ignored, and told so once.
    for comment_id in ("1", "2"):
        reply = await _comment(c, key, "/approve", "acc-stranger", comment_id)
        assert reply["handled"] is False and "not allowed" in reply["reason"]
    assert (await _wait(c, key))["pending"]["type"] == "requirements_approval"
    refusals = [text for text in fakes["comments"][key] if "may answer" in text]
    assert len(refusals) == 1, "one answer per person per gate, not one per comment"

    # No author at all is not an approver either.
    assert (await _comment(c, key, "/approve", "", "3"))["handled"] is False

    # The assignee may answer.
    assert (await _comment(c, key, "/approve", "acc-sam", "4"))["handled"] is True
    data = await _wait(c, key)
    assert data["pending"]["type"] == "plan_approval"
    assert data["decision_log"][-1]["gate"] == "requirements_approval"
    assert data["decision_log"][-1]["action"] == "approve" and data["decision_log"][-1]["by"] == "jira:acc-sam"


async def test_gate_approvers_setting_can_name_accounts(client, fakes, monkeypatch):
    from pi_jira_agent.config import settings

    c, _ = client
    monkeypatch.setattr(settings, "gate_approvers", "account:acc-lead")
    key = await _jira_run_at_requirements_gate(c)

    assert (await _comment(c, key, "/approve", "acc-ana", "1"))["handled"] is False, "the reporter is not listed now"
    assert (await _comment(c, key, "/approve", "acc-lead", "2"))["handled"] is True


async def test_ui_decision_is_logged(client):
    c, _ = client
    key = f"API-{uuid.uuid4().hex[:5].upper()}"
    await c.post("/api/runs", json={"issue_key": key})
    data = await _wait(c, key)
    r = await c.post(f"/api/runs/{key}/decision", json={"action": "approve", "gate_id": data["pending"]["gate_id"]})
    assert r.status_code == 200
    log = (await _wait(c, key))["decision_log"]
    assert [(e["gate"], e["action"], e["by"]) for e in log] == [("requirements_approval", "approve", "ui")]
    assert log[0]["at"]
