# Agents and MCP

AI agents use an index through **fdp-mcp**, the Model Context Protocol
bridge of the FAIR Data Point ecosystem
([FAIRDataTeam/mcp](https://github.com/FAIRDataTeam/mcp)). Pointed at an
index, it gives an agent tools to find FDPs and data across all of them, and
to continue on one FDP with the FDP-level tools it already has for single
FDPs (ADR-0018 of fdpneo-server, as amended on 2026-11-19). The index itself
runs no MCP server; the bridge uses its public API.

!!! note
    Index mode is in fdp-mcp from its contract 1.2.0 (draft); use a release
    that lists `INDEX_BASE_URL` in its configuration.

## Connecting

Run the bridge with the index's address:

```sh
docker run --rm -p 8080:8000 \
  -e INDEX_BASE_URL=https://index.example.org \
  ghcr.io/fairdatateam/fdp-mcp:main
```

and connect a streamable-HTTP client to `http://localhost:8080/mcp`. For a
desktop client that starts the bridge itself, use the `stdio` transport:

```jsonc
{
  "mcpServers": {
    "fdp-index": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "INDEX_BASE_URL=https://index.example.org",
        "-e", "FDP_MCP_TRANSPORT=stdio",
        "ghcr.io/fairdatateam/fdp-mcp:main"
      ]
    }
  }
}
```

`FDP_BASE_URL` may be set as well, for one FDP the session starts on.

## The tools

| Tool | What it does | Index API it uses |
|---|---|---|
| `index_info` | What the index holds: FDPs by state, resources by kind, the filters on offer, whether search, SPARQL and the catalogue feed are on | `/index/config`, `/index/search/landing` |
| `find_fdps` | FDPs by name, address and state, paged | `/index/entries` |
| `index_search` | The faceted search, typed filters included; each hit says its FDP and the profiles it meets | `/index/search` |
| `get_resource` | One resource as the index holds it: values with labels, parents, children, copies in other FDPs, its triples | `/index/search/resources/{id}` |
| `index_sparql_query` | A read-only SPARQL query over the harvested metadata, within the index's limits | `/index/sparql` |
| `get_conformance` | An FDP's conformance report, or one record's findings | `/index/conformance/…` |
| `open_fdp` | Point this session's FDP-level tools (`fdp_info`, `list_records`, `get_record` …) at an FDP the index lists as accepted and active | the FDP itself |

Hits and resources come as the bridge's Record objects with the index's
`id`, their `fdp` and their `conformance`: the specification and name of
each profile the index found them to meet.

## What the bridge may reach

- The index, always anonymously: an administrator's credentials are never
  sent to it, and agents do not administer the index.
- The FDPs the index lists as accepted and active, after `open_fdp`; the
  list is read from the index and read again after a minute
  (`FDP_MCP_INDEX_ALLOWLIST_TTL_SECONDS`). An FDP that leaves it is refused.
- Nothing else: IRIs in metadata are never dereferenced.

An agent sees what the index shows visitors. When an administrator shows
visitors only conforming records (**What visitors see**, see
[Administering](administering.md#conformance)), the agent's searches are
limited the same way.

## An example session

1. `index_info`: an index of 61 FDPs, search and conformance on.
2. `index_search` with `q: "asthma"`, `types: ["dataset"]` and the filter
   `conformance` = the HealthDCAT-AP specification: datasets about asthma that
   meet HealthDCAT-AP, each with its FDP.
3. `get_resource` on a hit: its values, distributions and copies.
4. `open_fdp` with that hit's `fdp`: then `get_record` and
   `get_access_conditions` on the FDP itself, which serves what the index does
   not harvest.
