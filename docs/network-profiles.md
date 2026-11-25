# Network profiles

A network profile says what an index expects of the FDPs it lists: how far
their metadata must reach, what each kind of record must have, and which
rules and tests apply. Every network writes it the same way, as one YAML
document, so an FDP's maintainer can read what a network asks before
registering, on the index's **Expectations** page.

The generic profile, which an index starts with, expects nothing beyond the
FDP specification. The index then judges FDPs by what they declare
themselves (`dct:conformsTo`), and shows how far their metadata reaches
without holding them to a level.

## The template

```yaml
profile: 1                     # the template's version

network:
  name: Example Network
  homepage: https://network.example.org/
  contact: mailto:fdp@network.example.org
  description: What the network is for, and what it finds through FDPs.

prefixes:                      # beyond the common ones (dcat, dct, foaf, …)
  ex: https://vocab.example.org/

reach: dataservice             # fdp | catalog | dataset | distribution | dataservice

levels:                        # per level: what counts as it, what it needs
  dataset:
    types: [ex:Registry]       # classes counted as datasets besides dcat:Dataset
    requires: [dct:title, dct:description, dcat:theme]
    recommends: [dcat:keyword]
  dataservice:
    requires: [dcat:endpointURL]

vocabularies:                  # where a property's values must come from
  dcat:theme: [http://publications.europa.eu/resource/authority/data-theme/]

shapes:                        # SHACL every record is checked against
  - https://network.example.org/shapes.ttl

benchmarks:                    # the network's own tests, run outside the index
  - https://example.org/benchmark/onboarding.ttl
```

Every part is optional. Properties and classes are CURIEs with a prefix the
index knows (`adms`, `dcat`, `dct`, `fdp-o`, `foaf`, `ldp`, `prof`, `r3d`,
`rdf`, `rdfs`, `schema`, `skos`, `vcard`) or one under `prefixes`, or full
IRIs. A profile that does not parse, or names an unknown prefix, is refused
with the reason. The JSON Schema is at `GET /index/network-profile/schema`.

## What each part does

| Part | Where it shows |
| --- | --- |
| `reach`, `levels.requires`, `vocabularies` | **Requirements** in each FDP's metadata health: "does not reach data services", "3 datasets lack `dct:description`". An FDP that misses one has findings, and the Statistics page counts it |
| `levels.recommends` | **Recommendations** in the metadata health; they do not count against the FDP |
| `levels.types` | Which records count as each level, in the health report and its reach |
| `shapes` | A conformance check of its own, **This network's rules**, required of every record. The documents are fetched under the index's outbound rules and kept for a day |
| `benchmarks` | Listed on the Expectations page. Running them through their runner (FAIR Champion) is TASKS 15.10 |

## Setting it

- **Settings → Network profile** edits the YAML, with the bundled profiles
  to start from: the generic one and ERDERA's.
- `FDPINDEX_NETWORK_PROFILE` installs a file at deployment; the settings
  page can replace it, and **Use the installed profile** returns to it.
- The API: `GET`, `PUT` (`{"source": "<yaml>"}`) and `DELETE
  /index/network-profile`; `GET /index/network-profile/examples`.

A change computes every FDP's health report again and checks its records
against the new shapes, in the background.

## ERDERA, as an example

`erdera.yaml` follows ERDERA's Onboarding Benchmark (FAIRsharing.2d49c0) and
its L1 and L2 metrics as published on 2026-11-23: datasets (biobanks and
patient registries included) need the core properties and the Virtual
Platform's `vpConnection` flag, and the metadata must reach data services,
through which the Virtual Platform finds Beacon endpoints. It is
illustrative: ERDERA's own definitions are authoritative.
