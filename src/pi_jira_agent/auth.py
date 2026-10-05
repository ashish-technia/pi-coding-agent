"""Who is calling the API.

Two modes, chosen by ``AUTH_MODE``:

- ``oidc``: every ``/api/*`` request carries ``Authorization: Bearer <access token>`` from the
  configured OpenID Connect issuer (Keycloak in the bundled compose file; any issuer works).
  The token's signature, issuer, audience and expiry are checked on every request, so the
  API keeps no session and any worker can answer any request.
- ``none``: no check at all. Only for a server bound to this machine; ``__main__`` refuses to
  start it on any other address.

Any signed-in user may use the whole API. Roles are a later requirement (R-26).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass

import httpx
import jwt

from .config import settings

logger = logging.getLogger(__name__)

# Paths under /api that must answer before anyone has signed in.
OPEN_API_PATHS = {"/api/auth/config"}
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class AuthError(Exception):
    """The request carries no usable identity (HTTP 401)."""


@dataclass(frozen=True)
class Identity:
    subject: str
    name: str = ""
    email: str = ""

    @property
    def label(self) -> str:
        """How this person appears in a run's decision log."""
        return f"ui:{self.email or self.name or self.subject}"


# With AUTH_MODE=none there is nobody to name; decisions are logged as plain "ui".
_LOCAL = Identity(subject="local")

_jwk_client: jwt.PyJWKClient | None = None


def needs_auth(path: str) -> bool:
    return path.startswith("/api/") and path not in OPEN_API_PATHS


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str) -> None:
    """Refuse to serve an unauthenticated API to the network."""
    if settings.auth_mode == "none" and not is_loopback(host):
        raise SystemExit(
            f"AUTH_MODE=none serves the API without sign-in and is only allowed on this machine, "
            f"not on {host}. Bind to 127.0.0.1, or set AUTH_MODE=oidc with OIDC_ISSUER, "
            "OIDC_CLIENT_ID and OIDC_AUDIENCE."
        )


def public_config() -> dict:
    """What the UI needs before it can sign anyone in. Nothing here is secret."""
    if settings.auth_mode != "oidc":
        return {"mode": "none"}
    return {
        "mode": "oidc",
        "issuer": settings.oidc_issuer.rstrip("/"),
        "client_id": settings.oidc_client_id,
        "scope": settings.oidc_scope,
    }


async def _jwks_url() -> str:
    if settings.oidc_jwks_url.strip():
        return settings.oidc_jwks_url.strip()
    discovery = f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(discovery)
        response.raise_for_status()
        return response.json()["jwks_uri"]


async def _signing_key(token: str):
    """The issuer's public key for this token. PyJWKClient caches the key set and refetches on an unknown key id."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(await _jwks_url(), cache_keys=True)
    return (await asyncio.to_thread(_jwk_client.get_signing_key_from_jwt, token)).key


async def identify(authorization: str | None) -> Identity:
    """The caller behind an ``Authorization`` header, or AuthError."""
    if settings.auth_mode == "none":
        return _LOCAL

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Sign in to use the API: send 'Authorization: Bearer <access token>'.")
    token = token.strip()
    try:
        key = await _signing_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=_ALGORITHMS,
            issuer=settings.oidc_issuer.rstrip("/"),
            audience=settings.oidc_audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # The reason goes to the log, not to the caller.
        logger.info("Rejected an access token: %s", exc)
        raise AuthError("The access token is not valid for this API, or it has expired.") from exc
    except httpx.HTTPError as exc:
        logger.error("Could not reach the OIDC issuer to verify a token: %s", exc)
        raise AuthError("The sign-in service could not be reached to verify the token.") from exc
    return Identity(
        subject=str(claims["sub"]),
        name=str(claims.get("name") or claims.get("preferred_username") or ""),
        email=str(claims.get("email") or ""),
    )
