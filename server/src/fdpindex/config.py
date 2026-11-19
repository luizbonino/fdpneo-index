"""Deployment configuration, read from ``FDPINDEX_*`` environment variables.

Runtime-tunable behaviour (rate limits, durations, deny list, auto-permit)
lives in the index settings (:mod:`fdpindex.settings`), editable by an
administrator. What is here is fixed for a deployment: where the database
is, who may administer, and how far outbound requests may reach.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Vocabulary hosts asked for concept labels when resolution is on (ADR-0006).
DEFAULT_LABEL_HOSTS = (
    "www.wikidata.org",
    "ror.org",
    "purl.obolibrary.org",
    "edamontology.org",
    "id.nlm.nih.gov",
    "publications.europa.eu",
    "orcid.org",
    "vocab.nerc.ac.uk",
    "id.loc.gov",
    "sws.geonames.org",
)


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
    # Where the server reads the issuer's configuration, when it cannot reach the
    # issuer's public address (Keycloak inside Compose: http://keycloak:8080/realms/…).
    # Tokens are still checked against oidc_issuer.
    oidc_discovery_url: str | None = None
    oidc_roles_claim: str = "realm_access.roles"
    oidc_admin_role: str = "admin"

    # --- outbound requests (ADR-0002) ----------------------------------------
    outbound_allow_private: bool = False
    outbound_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    outbound_max_redirects: int = Field(default=5, ge=0)
    harvest_max_nodes: int = Field(default=2000, gt=0)
    # Bounds per harvest, so one FDP cannot hold the job pool or fill the database.
    harvest_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    harvest_max_seconds: float = Field(default=900, gt=0)
    harvest_node_timeout_seconds: float = Field(default=20, gt=0)
    user_agent: str = "fdpneo-index (+https://github.com/luizbonino/fdpneo-index)"

    # --- background work -------------------------------------------------------
    worker_concurrency: int = Field(default=4, gt=0)
    # Liveness checks (ADR-0004): how often to look for due checks, and how many to
    # start per sweep. Whether checks run, and their interval, are index settings.
    health_sweep_seconds: float = Field(default=60, gt=0)
    health_batch: int = Field(default=100, gt=0)
    # Recorded HTTP bodies are cut at this size so the event log stays bounded.
    event_body_max_chars: int = Field(default=65536, ge=0)
    # SPARQL over the harvested metadata (search design §2.2): an embedded store
    # derived from PostgreSQL, queried in killable worker processes.
    sparql_enabled: bool = True
    sparql_dir: Path = Path("data/sparql")
    sparql_workers: int = Field(default=2, gt=0)
    sparql_timeout_seconds: float = Field(default=10, gt=0)
    sparql_admin_timeout_seconds: float = Field(default=60, gt=0)
    sparql_max_rows: int = Field(default=10_000, gt=0)
    sparql_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    sparql_max_query_chars: int = Field(default=20_000, gt=0)
    sparql_rate_per_minute: int = Field(default=30, ge=0)
    sparql_worker_max_mb: int = Field(default=1024, ge=0)
    sparql_void_interval_seconds: float = Field(default=60, ge=0)
    # Faceted search over the harvested metadata (search design §3 to §5): a projection
    # into PostgreSQL tables, kept in step with harvests.
    search_enabled: bool = True
    search_rate_per_minute: int = Field(default=120, ge=0)
    # Semantic search (TASKS 10.1): "local" runs the sentence model in this process;
    # needs the "semantic" extra, pgvector and a model (fdpindex embeddings download).
    search_embeddings: Literal["off", "local"] = "off"
    embeddings_model_dir: Path | None = None
    embeddings_threads: int = Field(default=0, ge=0)
    # A hit found by meaning alone must be this close (cosine distance; TASKS 10.1).
    embeddings_max_distance: float = Field(default=0.6, gt=0, le=2)
    search_timeout_seconds: float = Field(default=5, gt=0)
    search_admin_timeout_seconds: float = Field(default=30, gt=0)
    # Concept labels from vocabularies (TASKS 8.6, ADR-0006): off by default, because it
    # sends requests to third-party hosts. Only these hosts are asked, each hop of a fetch.
    labels_resolve: bool = False
    labels_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_LABEL_HOSTS)
    )
    labels_languages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["en", "nl", "de", "fr", "es", "it", "pt", "sv", "da", "fi"]
    )
    labels_refresh_days: int = Field(default=30, ge=1)
    labels_retry_days: int = Field(default=7, ge=1)
    labels_broader_depth: int = Field(default=2, ge=0, le=5)
    labels_interval_seconds: float = Field(default=60, gt=0)
    labels_requests_per_second: float = Field(default=2, gt=0)
    labels_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    # Public base URL (https://index.example.org), for absolute IRIs in the VoID description.
    public_url: str | None = None

    # --- web -----------------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "admin_tokens", "cors_origins", "labels_hosts", "labels_languages", mode="before"
    )
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
