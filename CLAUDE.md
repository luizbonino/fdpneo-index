# Claude Code context — fdpindex

The FDP Index as a standalone service: a directory of FAIR Data Points with
ping intake, metadata retrieval, reachability, harvest and administration.
It sits beside `../server` (FDPneo server), `../client` (fdpneo-client) and
`../mcp`, and does not import any of them.

## Read these first

1. [`TASKS.md`](TASKS.md): phases and status.
2. [`docs/adr/`](docs/adr/): ADR-0001 (standalone, compatible on the wire and
   with the reference API), ADR-0002 (harvested data in PostgreSQL, outbound
   safety), ADR-0003 (administrator authentication), ADR-0004 (active liveness
   checks, proposed).
3. The reference: `FAIRDataTeam/FAIRDataPoint`, package
   `org.fairdatateam.fairdatapoint.index`, and `FAIRDataTeam/FAIRDataPoint-client`.

## Ground rules

- **Compatibility is a contract.** `POST /` with `{"clientUrl"}` and the
  `/index/*` paths and JSON shapes of the reference stay as they are. New
  behaviour gets new paths or new fields. Durations are ISO-8601 in Java's
  `Duration.toString()` form.
- **Every outbound request goes through `OutboundClient`** (ADR-0002): no
  direct `httpx` calls elsewhere. Pings are unauthenticated, so anything the
  index fetches is attacker-chosen.
- **Deviations from the reference are documented** in the module docstring
  where they happen, and in the ADR when they are visible.
- **The event table is the durable work log.** Background jobs are in-process;
  a retrieval or webhook delivery must leave an unfinished event behind so it
  resumes after a restart.

## Stack and conventions

Server: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async + Alembic,
asyncpg, rdflib, httpx, structlog, PyJWT. Tooling as in fdpneo-server: `uv`,
`ruff` (lint + format), `pyright` strict, `pytest` (`unit` on in-memory
SQLite with one job at a time; `integration` on PostgreSQL via
testcontainers). Quality gate: ruff clean, pyright 0 errors, all tests green.

Client: Vue 3, TypeScript, Vite, TanStack Query, vue-i18n, oidc-client-ts, the
FAIR Ecosystem design tokens shared with fdpneo-client.

Commits are signed off (`git commit -s`). Stage files by name, never a
directory.
