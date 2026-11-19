# An MCP server for the index — proposal

- Status: **option A agreed 2026-11-19**; built after conformance shows in search results (question 3). Question 2 is open.
- Date: 2026-11-19
- Context: FDPneo ADR-0018 (the FDP-level bridge `fdp-mcp`, a sidecar) and
  its agent-consumption vision, increment C ("Index-level MCP: source
  discovery, capability resolution, query dispatch/aggregation"). The vision
  leaves one question open, to be decided by an ADR: **where the
  index-level MCP lives** (an `fdp-mcp` mode, a second sidecar beside the
  index, or part of the index deployment).

## What agents should be able to do

1. **Find FDPs**: by name, country, state and what they hold ("FDPs in the
   Netherlands with cancer registries").
2. **Search across FDPs**: the index's faceted search, with typed filters
   (`POST /index/search`), field prefixes, temporal and area filters, and
   copies grouped.
3. **Read a resource** as the index holds it: values with labels, parents,
   children, copies in other FDPs, the harvested triples.
4. **Query with SPARQL** over the harvested metadata, within the index's
   limits.
5. **Go to the source**: continue on one FDP with the FDP-level tools
   (`fdp_info`, `get_record`, `get_access_conditions` …), which see what the
   FDP itself serves, including what the index does not harvest.

Dispatching data queries (Beacon 2) and resolving capabilities across FDPs
need the capability profile (vision, increment B) and stay out of scope.

## Options

**A. An index mode of `fdp-mcp`.** The bridge gets a second target kind:
`INDEX_BASE_URL` instead of (or beside) `FDP_BASE_URL`. Index tools
(`index_info`, `find_fdps`, `search`, `get_resource`, `sparql_query`) call the
index's public HTTP API. "Go to the source" switches the FDP-level tools to
an FDP the index lists, so egress is the index plus the FDPs it has
accepted and found reachable.

- For: one MCP codebase and one tool contract (envelopes, errors, the
  `unsupported` result, record objects) for both levels; ADR-0018's reasons
  carry over (protocol churn and security review stay in the sidecar; reach:
  it works against any deployment of the index); the dispatch step reuses
  the FDP tools unchanged.
- Against: a change in `fdp-mcp` (FAIRDataTeam), not in this repository; the
  index's HTTP API becomes a contract the bridge depends on (it is
  documented and versioned with OpenAPI, so this is a cost, not a risk);
  egress becomes dynamic (an allowlist taken from the index).

**B. A second sidecar in this repository.** The same tools, built on the MCP
SDK, beside the index.

- For: the index's team owns it end to end.
- Against: two MCP codebases with diverging conventions; the dispatch step
  duplicates the FDP tools or calls `fdp-mcp` anyway.

**C. Inside the index server** (`/mcp`, streamable HTTP).

- For: one deployable; the tools call the search service directly, with
  the index's own authentication and rate limits.
- Against: the MCP SDK and its protocol churn enter the index server, which
  ADR-0018 deliberately kept out of FDPneo's server; the dispatch step
  needs the FDP tools inside the index too.

## Recommendation

**A, an index mode of `fdp-mcp`**, specified in `mcp-tool-surface.md` as a
second tool group:

| Tool | Backed by | Notes |
|---|---|---|
| `index_info` | `GET /index/config`, `/index/search/landing` | counts, facets on offer, whether SPARQL and semantic search are on |
| `find_fdps` | `GET /index/entries` | name, country, state; paged |
| `search` | `POST /index/search` | typed filters, facets, `collapse`, `cursor`; hits as Record objects with their FDP |
| `get_resource` | `GET /index/search/resources/{id}` | values with labels, copies, `?format=` for triples |
| `sparql_query` | `/index/sparql` | the same read-only gate and limits as the FDP-level tool |
| `open_fdp` | the FDP-level tools | sets the FDP for this session's FDP tools; only FDPs the index lists as accepted and reachable |

Credentials: anonymous by default, as the index's public surface is; an
administrator's token is not forwarded (agents do not administer the
index).

## Questions for review

1. A, B or C? (Recommendation: A.)
2. Is `open_fdp` (a session switching the FDP-level tools to another FDP)
   acceptable, or should FDP-level tools take the FDP as a parameter?
3. Should the index mode come with `fdp-mcp`'s next release, or wait until
   the conformance work (Phase 11) can report conformance in `search`
   results?

**Answers (2026-11-19).** 1: A, an index mode of `fdp-mcp`. 3: wait until
search results report conformance. 2: open (switching a session's FDP with
`open_fdp`, or naming the FDP on every FDP-level call).

