"""Administrator authentication (ADR-0003)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from fdpindex.auth import ANONYMOUS, Authenticator, OidcVerifier, new_token, token_digest
from fdpindex.config import Settings
from fdpindex.errors import Unauthorized

pytestmark = pytest.mark.unit

ISSUER = "https://idp.example.org/realms/fdp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Keys:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return jwt.PyJWK.from_dict(
            {**RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1"}
        )


def _token(roles: list[str], **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "fdpindex",
        "sub": "u1",
        "preferred_username": "alice",
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
        **overrides,
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


def _authenticator(**settings: Any) -> Authenticator:
    verifier = OidcVerifier(ISSUER, "fdpindex", httpx.AsyncClient())
    verifier._jwks = _Keys()  # pyright: ignore[reportAttributeAccessIssue]
    return Authenticator(
        Settings(oidc_issuer=ISSUER, oidc_audience="fdpindex", **settings), verifier
    )


async def test_no_header_is_anonymous() -> None:
    assert await _authenticator().principal(None) is ANONYMOUS


async def test_oidc_admin_role() -> None:
    principal = await _authenticator().principal(f"Bearer {_token(['admin'])}")
    assert principal.is_admin
    assert principal.name == "alice"
    plain = await _authenticator().principal(f"Bearer {_token(['steward'])}")
    assert not plain.is_admin


async def test_custom_roles_claim_and_role() -> None:
    authenticator = _authenticator(oidc_roles_claim="groups", oidc_admin_role="index-admins")
    token = _token([], groups=["index-admins"])
    assert (await authenticator.principal(f"Bearer {token}")).is_admin


@pytest.mark.parametrize(
    "overrides",
    [{"exp": int(time.time()) - 10}, {"iss": "https://evil.example.org"}, {"aud": "other"}],
)
async def test_invalid_tokens_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(Unauthorized):
        await _authenticator().principal(f"Bearer {_token(['admin'], **overrides)}")


async def test_static_tokens() -> None:
    token, digest = new_token()
    assert token.startswith("fdpi_") and digest == token_digest(token)
    authenticator = Authenticator(Settings(admin_tokens=[digest.upper()]), None)
    assert (await authenticator.principal(f"Bearer {token}")).is_admin
    with pytest.raises(Unauthorized):
        await authenticator.principal("Bearer something-else")
    with pytest.raises(Unauthorized):
        await authenticator.principal(f"Basic {token}")


def test_comma_separated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDPINDEX_ADMIN_TOKENS", "AA, bb ,")
    monkeypatch.setenv("FDPINDEX_CORS_ORIGINS", "https://a.example,https://b.example")
    settings = Settings()
    assert settings.admin_tokens == ["aa", "bb"]
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


async def test_discovery_can_come_from_an_internal_address() -> None:
    """Inside Compose the server reads Keycloak's configuration at keycloak:8080, while
    tokens name the public issuer."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"jwks_uri": "http://keycloak:8080/realms/fdp/certs"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    internal = "http://keycloak:8080/realms/fdp/.well-known/openid-configuration"
    verifier = OidcVerifier(ISSUER, "fdpindex", http, discovery_url=internal)
    client = await verifier._client()  # pyright: ignore[reportPrivateUsage]
    assert asked == [internal]
    assert client.uri == "http://keycloak:8080/realms/fdp/certs"
    default = OidcVerifier(ISSUER, None, http)
    await default._client()  # pyright: ignore[reportPrivateUsage]
    assert asked[-1] == f"{ISSUER}/.well-known/openid-configuration"
