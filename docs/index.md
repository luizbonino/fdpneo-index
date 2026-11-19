# FDPneo Index

The FDPneo Index is a directory and search engine for
[FAIR Data Points](https://www.fairdatapoint.org/). FAIR Data Points (FDPs)
register by pinging it; it checks they are reachable, harvests their
metadata, checks that metadata against profiles such as DCAT-AP and
HealthDCAT-AP, and makes all of it searchable by people, by programs and by
AI agents. It speaks the API of the Java FAIR Data Point's index, so existing
FDPs and clients work with it unchanged.

## Where to start

| You want to | Read |
|---|---|
| **Find data** in an index: search, filters, SPARQL | [Finding data](user-guide.md) |
| **List your FDP** in an index, and see how its metadata does | [FDPs and the index](data-holders.md); for FDPneo servers, [FDPneo servers](with-fdpneo.md) |
| **Administer** an index: approvals, settings, search filters, conformance, webhooks | [Administering an index](administering.md) |
| **Run** an index: install, configure, back up, upgrade | [Deploying](deploying.md), [Configuration](configuration.md), [Operations](operations.md) |
| Replace a **Java FDP's index** | [Replacing a Java FDP's index](java-fdp.md) |
| **Integrate**: search, SPARQL, the DCAT-AP feed, OAI-PMH from your code | [API examples](api.md) and the [API reference](api-reference.md) |
| Let **AI agents** use the index | [Agents and MCP](agents.md) |
| Understand **why** it works as it does | The decisions (ADRs) and design notes under *Decisions and design* |

## What it does

- **A directory** of FDPs: pings, approval, liveness checks, webhooks, the
  Java index's API.
- **Harvest** of each FDP's metadata into PostgreSQL, with conditional
  requests and safety rules for every outbound request.
- **Search** over all harvested metadata: full text in 16 languages,
  filters, temporal and spatial coverage, copies across FDPs grouped, and
  semantic search as an option.
- **SPARQL** over the harvested metadata, with an editor, a query builder and
  an example library.
- **Conformance**: every record checked against SHACL profiles (DCAT-AP 3,
  HealthDCAT-AP, Health-RI core, or your own), with a report per FDP for its
  data holders.
- **Republication**: the harvested metadata as a DCAT-AP catalogue and over
  OAI-PMH, for aggregators such as data.europa.eu.
- **An interface** in English, Dutch, German, French, Spanish, Italian and
  Portuguese.

The source code and the issue tracker are on
[GitHub](https://github.com/luizbonino/fdpneo-index).
