"""Deployment configuration, read from ``FDPINDEX_*`` environment variables.

Runtime-tunable behaviour (rate limits, durations, deny list, auto-permit)
lives in the index settings (:mod:`fdpindex.settings`), editable by an
administrator. What is here is fixed for a deployment: where the database
is, who may administer, and how far outbound requests may reach.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FDPINDEX_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "production"] = "production"
    database_url: str = "postgresql+asyncpg://fdpindex:fdpindex@localhost:5432/fdpindex"

    # The initial value of the ``autoPermit`` index setting (the reference's
    # FDP_INDEX_AUTOPERMIT, default true). An administrator can change it at runtime.
    auto_permit: bool = True

    # --- administrators (ADR-0003) -------------------------------------------
    # SHA-256 hex digests of static bearer tokens; ``fdpindex token new`` makes one.
    admin_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_client_id: str | None = None  # handed to the web client, not used by the server
    oidc_roles_claim: str = "realm_access.roles"
    oidc_admin_role: str = "admin"

    # --- outbound requests (ADR-0002) ----------------------------------------
    outbound_allow_private: bool = False
    outbound_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    outbound_max_redirects: int = Field(default=5, ge=0)
    harvest_max_nodes: int = Field(default=2000, gt=0)
    user_agent: str = "fdpindex (+https://github.com/FAIRDataTeam/fdpindex)"

    # --- background work -------------------------------------------------------
    worker_concurrency: int = Field(default=4, gt=0)
    # Recorded HTTP bodies are cut at this size so the event log stays bounded.
    event_body_max_chars: int = Field(default=65536, ge=0)

    # --- web -----------------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("admin_tokens", "cors_origins", mode="before")
    @classmethod
    def _comma_separated(cls, value: object) -> object:
        """Environment variables carry lists as comma-separated strings."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("admin_tokens")
    @classmethod
    def _lowercase_digests(cls, value: list[str]) -> list[str]:
        return [digest.lower() for digest in value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
