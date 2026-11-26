# FDPs and the index

For the people who run a FAIR Data Point (FDP) or publish metadata in one:
how an FDP gets into an index, what the index does with its metadata, and
how to read the conformance report.

## Getting listed

An FDP joins an index by **pinging** it: a `POST` of `{"clientUrl": "<your
FDP's address>"}` to the index's root. The Java FAIR Data Point and FDPneo
servers ping by themselves when they are configured with the index's
address (for FDPneo, see [FDPneo servers](with-fdpneo.md)); any other FDP can
be registered on the index's **Register** page, which pings for you and
shows at once what the index found.

**Check first.** Before registering, the Register page's **Check first**
reads your FDP as the index would: whether its address answers with an
FDP's metadata, and the metadata health (below) of a sample of its pages,
up to 200 pages and 5 children of each page, so the sample goes down every
level. Nothing is stored and the FDP is not registered. Anyone may check
when the index lists FDPs without an administrator; otherwise
administrators check a waiting FDP from its page, while they review it.

What happens next depends on the index's approval setting: the FDP is
listed at once, after its metadata was retrieved successfully, or after an
administrator accepted it. FDPs ping again from time to time (the Java FDP
once a week); an FDP that stops pinging and cannot be reached is marked
*unreachable*, and some indexes hide FDPs out of contact for months.

## What the index harvests

Once accepted, the index **harvests** the FDP: it reads the FDP's own
description, follows the links to catalogues, datasets, distributions and
data services as the FDP specification lays them out (`ldp:contains`, or
`fdp-o:metadataCatalog`, `dcat:dataset` and `dcat:distribution` on pages
without LDP containers), and stores each page it reads. A record linked only
by another DCAT property, such as a data service named only in a
distribution's `dcat:accessService`, is not harvested: list it in an LDP
container, as the FDP software does. It harvests again
after every ping and when a scheduled check finds the FDP's own description
changed; an administrator can add a fixed interval for FDPs whose datasets
change without their root changing.

- **Only your FDP's origin** is read: links to other hosts are not followed.
- **Conditional requests** (`ETag`, `Last-Modified`) save both sides work:
  serve them if you can.
- **Large FDPs** are read in runs of up to 2 000 pages, a few minutes apart,
  until every page has been read (up to 100 000 by default; an administrator
  can allow more). Unchanged pages answer `304 Not Modified`, so later
  harvests are quick. A page that cannot be read keeps its previous version.
- **Asking the index to wait**: answer `429 Too Many Requests`, or `503` with
  `Retry-After`, and the index stops and comes back when `Retry-After` says.
  It reads one page at a time.
- **Nothing is rewritten.** The index shows, searches and republishes your
  metadata as you serve it; titles and labels are shown in the reader's
  language when you provide it.

**Your FDP's page** in the index (the directory, then your FDP) shows its
state, the events of its pings and checks, how many pages were harvested,
and the harvested metadata to preview or download.

## Metadata health

Your FDP's page has a **Metadata health** section, worked out after every
harvest; the Statistics page sums it over all FDPs and has a table with CSV
download. It shows how far your metadata reaches (FDP → catalogues →
datasets → distributions → data services, with the pages at each level),
the pages that could not be read and why, and where the index stopped by
its own rules (links to other sites, a page limit). Who may see it follows
the same setting as the conformance report.

**Findings** are about what every FDP takes on by being one, the FDP
specification. They mark your FDP as having findings:

- **Linked only through DCAT.** A record is linked from another (for
  example with `dcat:accessService`) but is in no LDP container. The index
  follows your FDP's containers, as the FDP specification says, so it does
  not harvest it. List it in its parent's container, as the FDP software
  does when you create it as a child.
- **Listed only in an LDP container.** The parent lists a record in its LDP
  container but does not link it with the container's member relation (for
  example `dcat:dataset`). Tools that read DCAT alone do not find it. Add
  that link to the parent's metadata.
- **Catalogue without datasets or services.** Add datasets or data services
  to it, or remove a catalogue that is no longer used.

**Notes** are DCAT's advice, which your FDP never promised. They are shown
with how to act on them, but do not count against your FDP:

- **Dataset without distributions.** A distribution says how to get the
  data: a download, an API or a landing page.
- **Distribution without access.** Give it a `dcat:accessURL`, a
  `dcat:downloadURL` or a `dcat:accessService`.

An empty LDP container is not a problem: the FDP software makes one for
every kind of child a record may have.

## The network's benchmarks

When the index's network profile names benchmarks (FAIR Testing Resources,
such as ERDERA's Virtual Platform Onboarding Benchmark) and an administrator
has switched runs on, your FDP's page has a **The network's benchmarks**
section. The index sends each of your records the benchmark applies to (its
address only) to the runner the benchmark names, FAIR Champion, which runs
the network's tests on it as anyone could on FAIR Champion's own page.

For each condition of the benchmark ("Minimal metadata requirements for L1
are met") you see how many of your records meet it, the result on your FDP
itself, and the records that do not meet it, with the network's guidance
links. **Tests and logs** shows each test's verdict and its log, which says
what the test looked for and did not find. The FAIR Testing Resources result
set is published as it came.

This is the network's verdict, not the index's: it does not change your
metadata health. A record is assessed again only after it changes, so fix
the record and wait for the next harvest; an administrator can also run the
benchmark on one record at once. "Could not be assessed" means the runner
did not answer, never that your record failed.

## Why my metadata looks as it does in search

- **The title is the page address**: the resource has no `dct:title`,
  `rdfs:label`, `skos:prefLabel`, `foaf:name` or `schema:name`.
- **A theme or licence shows as an address, not a name**: the index labels
  the EU vocabularies, SPDX licences and common terms itself; for other
  vocabularies it needs the label in your metadata, or the index's
  administrator to switch on label resolution.
- **A dataset is listed twice, under two FDPs**: the same DOI, identifier or
  `owl:sameAs` in several FDPs makes them copies; search shows "also in"
  and can group them.
- **Text in the wrong language**: give literals language tags
  (`"Cancer registry"@en`, `"Kankerregister"@nl`).
- **Your FDP is in the wrong country on the Statistics map**, or on none:
  state it in your FDP's metadata with `r3d:country`, as an EU country IRI
  (`<http://publications.europa.eu/resource/authority/country/NLD>`) or an
  ISO code (`"NL"`). Otherwise the index guesses from your domain or your
  server's address, and a server in another country places you there.
- **Your datasets are not on the "where the data is about" map**: give them
  `dct:spatial` with an EU country IRI or a NUTS region
  (`<http://data.europa.eu/nuts/code/NL33>`).

## The conformance report

The index checks every harvested **record** (a catalogue, dataset, dataset
series or data service, with its distributions and the descriptions it
holds inline) against **what it declares**: the profiles it names with
`dct:conformsTo`. The FDP software names its own profile on every record;
the index reads that profile's SHACL from your FDP and checks the record
against it. A record that names nothing declares no profile, which is not
held against it. Some indexes also **require** profiles of their own
(HealthDCAT-AP in health indexes, for instance), or offer other profiles as
**lenses** visitors may look through; the report says which checks are your
declarations, which are the index's requirements, and which are lenses you
did not claim. A
record **conforms** to a profile when it has no **violations**;
**warnings** are the profile's recommendations.

Your FDP's page has a **Conformance** section: per profile, how many records
conform, and the most frequent problems. **Show records** lists the records
with a problem; each record's page lists its findings: the property, what
is wrong, and the profile's own explanation when it gives one. Checks run in
the background after each harvest, so a fix shows after your FDP's next
harvest (ping the index, or ask its administrator to check it now).

Some indexes show visitors only what conforms, in search, SPARQL, the list
of FDPs or the catalogue feed.

### Common problems and how to fix them

From checking the 61 reachable FDPs listed at home.fairdatapoint.org
(November 2026):

| Problem | Fix |
|---|---|
| `dct:hasVersion` given as text (`"1.0"`): a violation in DCAT-AP 3 | DCAT-AP 3 uses `dcat:version` for a version number; `dct:hasVersion` links to another version of the dataset (an IRI) |
| `dct:language` from the Library of Congress ISO 639 list (`http://id.loc.gov/vocabulary/iso639-1/en`) | Use the EU language table: `http://publications.europa.eu/resource/authority/language/ENG` |
| `dct:publisher` "does not meet its rules" | The publisher needs a `foaf:name` (and, for HealthDCAT-AP and Health-RI, contact details) in the dataset's own page. A publisher described only on its own page of your FDP is not part of the dataset's record |
| `dcat:distribution` missing | DCAT-AP requires a distribution with a `dcat:accessURL` for each dataset |
| Missing `dct:description` | Catalogues and datasets need a description |
| HealthDCAT-AP: `healthdcatap:hdab`, `healthdcatap:healthCategory`, `dcatap:applicableLegislation` missing | The health properties HealthDCAT-AP makes mandatory; the [HealthDCAT-AP specification](https://healthdataeu.pages.code.europa.eu/healthdcat-ap/releases/release-8/) explains each |
| A value "not from the expected vocabulary" | The profile expects a concept from an EU table (data themes, access rights, frequencies, file types …): use its IRI |

You can check a file yourself before publishing it, with the index's
command line (see [Administering](administering.md#conformance)) or the EU's
own validators for DCAT-AP and HealthDCAT-AP.

## Republication

An index can republish what it harvested as a DCAT-AP catalogue
(`/index/catalog`, and over OAI-PMH at `/index/oai`), so that aggregators such
as data.europa.eu find your datasets. Each record there points back to your
FDP (`dct:source`) and keeps your dataset's own IRI; nothing is rewritten.
