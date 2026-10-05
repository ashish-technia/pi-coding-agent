"""Sign-in on the API: who gets in with AUTH_MODE=oidc, and what stays open."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from pi_jira_agent import auth
from pi_jira_agent.config import Settings, settings

pytestmark = pytest.mark.asyncio

ISSUER = "https://sso.example.test/realms/pi-jira-agent"
AUDIENCE = "pi-jira-agent-api"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(*, key=_KEY, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "email": "alice@example.com",
        "name": "Alice",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def bearer(value: str) -> dict:
    return {"Authorization": f"Bearer {value}"}


@pytest.fixture
async def oidc_client(fakes, monkeypatch):
    """The app with AUTH_MODE=oidc, trusting tokens signed by the test key."""
    from pi_jira_agent import main as main_mod

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", "pi-jira-agent-ui")
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)

    async def signing_key(_token):  # stands in for the issuer's published key set
        return _KEY.public_key()

    monkeypatch.setattr(auth, "_signing_key", signing_key)

    svc = main_mod.AutomationService()
    monkeypatch.setattr(main_mod, "automation", svc)
    await svc.start()
    try:
        async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as client:
            yield client
    finally:
        await svc.stop()


async def test_api_needs_a_valid_token(oidc_client):
    c = oidc_client
    for path in ("/api/runs", "/api/config", "/api/runs/TEST-1"):
        r = await c.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", path
    r = await c.post("/api/runs", json={"issue_key": "TEST-1"})
    assert r.status_code == 401, "an unauthenticated caller cannot start a run"

    assert (await c.get("/api/runs", headers=bearer(token()))).status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"key": _OTHER_KEY}, id="signed by someone else"),
        pytest.param({"exp": int(time.time()) - 60}, id="expired"),
        pytest.param({"aud": "some-other-api"}, id="meant for another API"),
        pytest.param({"iss": "https://evil.example.test/realms/x"}, id="from another issuer"),
        pytest.param({"sub": None}, id="no subject"),
    ],
)
async def test_tokens_that_must_not_get_in(oidc_client, bad):
    r = await oidc_client.get("/api/runs", headers=bearer(token(**bad)))
    assert r.status_code == 401
    assert "not valid" in r.json()["detail"], "the caller is not told which check failed"


async def test_malformed_authorization_headers_are_refused(oidc_client):
    for header in ({"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer not-a-jwt"}):
        assert (await oidc_client.get("/api/runs", headers=header)).status_code == 401


async def test_what_stays_open_without_a_token(oidc_client):
    c = oidc_client
    assert (await c.get("/health")).status_code == 200
    r = await c.get("/api/auth/config")
    assert r.status_code == 200
    assert r.json() == {
        "mode": "oidc",
        "issuer": ISSUER,
        "client_id": "pi-jira-agent-ui",
        "scope": "openid profile email",
    }
    # The webhooks keep their own shared secret.
    r = await c.post("/webhooks/jira/trigger", json={"issue_key": "TEST-1"})
    assert r.status_code == 401 and "webhook secret" in r.json()["detail"]


async def test_old_unprefixed_routes_are_gone(oidc_client):
    c = oidc_client
    r = await c.post("/run", json={"issue_key": "TEST-1"}, headers=bearer(token()))
    assert r.status_code in {404, 405}, "the legacy start route would have been a way in without /api"
    r = await c.get("/runs/TEST-1/status", headers={"accept": "application/json"})
    assert "status" not in (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


async def test_decision_records_the_signed_in_user(oidc_client):
    c = oidc_client
    key = f"AUTH-{uuid.uuid4().hex[:5].upper()}"
    headers = bearer(token())
    assert (await c.post("/api/runs", json={"issue_key": key}, headers=headers)).status_code == 200

    import asyncio

    for _ in range(500):
        data = (await c.get(f"/api/runs/{key}", headers=headers)).json()
        if not data["running"]:
            break
        await asyncio.sleep(0.02)
    r = await c.post(
        f"/api/runs/{key}/decision",
        json={"action": "cancel", "gate_id": data["pending"]["gate_id"]},
        headers=headers,
    )
    assert r.status_code == 200
    for _ in range(500):
        data = (await c.get(f"/api/runs/{key}", headers=headers)).json()
        if not data["running"]:
            break
        await asyncio.sleep(0.02)
    assert data["decision_log"][-1]["by"] == "ui:alice@example.com"


async def test_auth_config_in_local_mode_says_so(fakes):
    from pi_jira_agent import main as main_mod

    async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as c:
        assert (await c.get("/api/auth/config")).json() == {"mode": "none"}


async def test_no_sign_in_is_only_served_to_this_machine(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "none")
    for host in ("127.0.0.1", "localhost", "::1"):
        auth.check_bind(host)
    for host in ("0.0.0.0", "192.168.1.20", "::"):
        with pytest.raises(SystemExit, match="AUTH_MODE=none"):
            auth.check_bind(host)

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    auth.check_bind("0.0.0.0")


async def test_oidc_mode_needs_its_settings():
    with pytest.raises(ValueError, match="OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_AUDIENCE"):
        Settings(auth_mode="oidc", oidc_issuer="", oidc_client_id="", oidc_audience="")  # pyright: ignore[reportCallIssue]
