"""Who is calling: anonymous, or an administrator (ADR-0003).

An administrator presents a bearer token that is either one of the static
admin tokens (compared by SHA-256 digest) or an OIDC access token from the
configured issuer whose roles claim holds the admin role. Any other token is
refused with 401 rather than silently treated as anonymous, so a client with
an expired token notices.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import structlog
from fastapi import Depends, Request

from fdpindex.errors import Forbidden, Unauthorized

if TYPE_CHECKING:
    from fdpindex.config import Settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Principal:
    name: str
    is_admin: bool

    @property
    def anonymous(self) -> bool:
        return self.name == "anonymous"


ANONYMOUS = Principal(name="anonymous", is_admin=False)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    """A fresh admin token and the digest to put in ``FDPINDEX_ADMIN_TOKENS``."""
    token = "fdpi_" + secrets.token_urlsafe(32)
    return token, token_digest(token)


def _claim(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)  # pyright: ignore[reportUnknownMemberType]
    return value


class OidcVerifier:
    """Validates access tokens against the issuer's JWKS (discovered once, keys cached)."""

    def __init__(self, issuer: str, audience: str | None, http: httpx.AsyncClient) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._http = http
        self._jwks: jwt.PyJWKClient | None = None
        self._lock = asyncio.Lock()

    async def _client(self) -> jwt.PyJWKClient:
        async with self._lock:
            if self._jwks is None:
                response = await self._http.get(
                    f"{self._issuer}/.well-known/openid-configuration", timeout=10
                )
                response.raise_for_status()
                self._jwks = jwt.PyJWKClient(str(response.json()["jwks_uri"]), cache_keys=True)
            return self._jwks

    async def verify(self, token: str) -> dict[str, Any]:
        client = await self._client()
        key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
            audience=self._audience,
            issuer=self._issuer,
            options={"verify_aud": self._audience is not None},
        )


class Authenticator:
    def __init__(self, settings: Settings, verifier: OidcVerifier | None) -> None:
        self._digests = list(settings.admin_tokens)
        self._verifier = verifier
        self._roles_claim = settings.oidc_roles_claim
        self._admin_role = settings.oidc_admin_role

    @property
    def configured(self) -> bool:
        return bool(self._digests) or self._verifier is not None

    async def principal(self, authorization: str | None) -> Principal:
        if not authorization:
            return ANONYMOUS
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("Unsupported authorization scheme", headers=_challenge())
        token = token.strip()
        digest = token_digest(token)
        for known in self._digests:
            if hmac.compare_digest(digest, known):
                return Principal(name="admin-token", is_admin=True)
        if self._verifier is not None:
            try:
                claims = await self._verifier.verify(token)
            except (jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as err:
                log.info("token_rejected", reason=str(err))
                raise Unauthorized("Invalid or expired token", headers=_challenge()) from err
            roles = _claim(claims, self._roles_claim)
            is_admin = isinstance(roles, list) and self._admin_role in roles
            name = str(claims.get("preferred_username") or claims.get("sub") or "oidc")
            return Principal(name=name, is_admin=is_admin)
        raise Unauthorized("Invalid or expired token", headers=_challenge())


def _challenge() -> dict[str, str]:
    return {"WWW-Authenticate": 'Bearer realm="fdpindex"'}


async def current_principal(request: Request) -> Principal:
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.principal(request.headers.get("authorization"))
    request.state.principal = principal
    return principal


async def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.anonymous:
        raise Unauthorized("Authentication required", headers=_challenge())
    if not principal.is_admin:
        raise Forbidden("Administrator role required")
    return principal
