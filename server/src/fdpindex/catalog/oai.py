"""OAI-PMH 2.0 over the catalogue feed (TASKS 11.5; conformance design §6).

data.europa.eu prefers OAI-PMH for harvesting a catalogue (its harvester uses
``ListRecords``). This is a read-only repository with one metadata format,
``dcat_ap``: each record is RDF/XML holding the ``dcat:CatalogRecord`` and
the resource's harvested triples, as the feed publishes them.

* Verbs: ``Identify``, ``ListMetadataFormats``, ``ListSets`` (no sets),
  ``ListIdentifiers``, ``ListRecords``, ``GetRecord``.
* Datestamps are when the index last saw the record change (seconds, UTC);
  ``from`` and ``until`` select on them. No deleted records are kept.
* Resumption tokens carry the keyset position and the selection, so a list
  neither repeats nor skips records while harvests happen.
* Identifiers: ``oai:<host>:<id>``, the id the search and the feed use.
"""

from __future__ import annotations

import base64
import binascii
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rdflib import Graph

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from fdpindex.catalog.feed import CatalogFeed, FeedRecord

OAI = "http://www.openarchives.org/OAI/2.0/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA = "http://www.openarchives.org/OAI/2.0/ http://www.openarchives.org/OAI/2.0/OAI-PMH.xsd"
PREFIX = "dcat_ap"
PAGE = 100
VERBS = {
    "Identify": set[str](),
    "ListMetadataFormats": {"identifier"},
    "ListSets": {"resumptionToken"},
    "ListIdentifiers": {"metadataPrefix", "from", "until", "set", "resumptionToken"},
    "ListRecords": {"metadataPrefix", "from", "until", "set", "resumptionToken"},
    "GetRecord": {"identifier", "metadataPrefix"},
}

ET.register_namespace("", OAI)
ET.register_namespace("xsi", XSI)


class OaiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value: str, *, end: bool) -> datetime:
    try:
        if len(value) == 10:
            day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            return day.replace(hour=23, minute=59, second=59) if end else day
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as err:
        raise OaiError("badArgument", f"not a UTC datestamp: {value}") from err


def _token(after: int, since: str | None, until: str | None, total: int) -> str:
    payload = json.dumps({"a": after, "f": since, "u": until, "n": total}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _untoken(token: str) -> tuple[int, str | None, str | None, int]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        data = json.loads(raw)
        return int(data["a"]), data.get("f"), data.get("u"), int(data.get("n", 0))
    except (ValueError, KeyError, TypeError, binascii.Error) as err:
        raise OaiError("badResumptionToken", "the resumption token is not valid") from err


class OaiRepository:
    def __init__(
        self,
        feed: CatalogFeed,
        *,
        base_url: str,
        host: str,
        catalog_iri: str,
        record_iri: Callable[[str], str],
        admin_email: str | None,
    ) -> None:
        self._feed = feed
        self._base_url = base_url
        self._prefix = f"oai:{host}:"
        self._catalog_iri = catalog_iri
        self._record_iri = record_iri
        self._admin_email = admin_email

    async def respond(self, args: Mapping[str, str]) -> bytes:
        root = ET.Element(f"{{{OAI}}}OAI-PMH", {f"{{{XSI}}}schemaLocation": SCHEMA})
        ET.SubElement(root, f"{{{OAI}}}responseDate").text = _stamp(datetime.now(UTC))
        request = ET.SubElement(root, f"{{{OAI}}}request")
        request.text = self._base_url
        verb = args.get("verb", "")
        try:
            if verb not in VERBS:
                raise OaiError("badVerb", "the verb is missing or not an OAI-PMH verb")
            extra = set(args) - VERBS[verb] - {"verb"}
            if extra:
                raise OaiError(
                    "badArgument", f"not an argument of {verb}: {', '.join(sorted(extra))}"
                )
            if "resumptionToken" in args and len(args) > 2:
                raise OaiError("badArgument", "resumptionToken is an exclusive argument")
            for key, value in args.items():
                request.set(key, value)
            body = await getattr(self, f"_{verb}")(args)
            root.append(body)
        except OaiError as err:
            ET.SubElement(root, f"{{{OAI}}}error", {"code": err.code}).text = err.message
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")

    # --- verbs -------------------------------------------------------------------

    async def _Identify(self, _args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        node = ET.Element(f"{{{OAI}}}Identify")
        earliest = await self._feed.earliest()
        for name, value in (
            ("repositoryName", self._feed.meta.title),
            ("baseURL", self._base_url),
            ("protocolVersion", "2.0"),
        ):
            ET.SubElement(node, f"{{{OAI}}}{name}").text = value
        if self._admin_email:
            ET.SubElement(node, f"{{{OAI}}}adminEmail").text = self._admin_email
        ET.SubElement(node, f"{{{OAI}}}earliestDatestamp").text = _stamp(
            earliest or datetime.now(UTC)
        )
        ET.SubElement(node, f"{{{OAI}}}deletedRecord").text = "no"
        ET.SubElement(node, f"{{{OAI}}}granularity").text = "YYYY-MM-DDThh:mm:ssZ"
        return node

    async def _ListMetadataFormats(self, args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        if "identifier" in args:
            await self._get(args["identifier"])
        node = ET.Element(f"{{{OAI}}}ListMetadataFormats")
        fmt = ET.SubElement(node, f"{{{OAI}}}metadataFormat")
        ET.SubElement(fmt, f"{{{OAI}}}metadataPrefix").text = PREFIX
        ET.SubElement(fmt, f"{{{OAI}}}schema").text = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        ET.SubElement(fmt, f"{{{OAI}}}metadataNamespace").text = "http://www.w3.org/ns/dcat#"
        return node

    async def _ListSets(self, _args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        raise OaiError("noSetHierarchy", "this repository has no sets")

    async def _ListIdentifiers(self, args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        return await self._list(args, records=False)

    async def _ListRecords(self, args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        return await self._list(args, records=True)

    async def _GetRecord(self, args: Mapping[str, str]) -> ET.Element:  # noqa: N802
        if "identifier" not in args or "metadataPrefix" not in args:
            raise OaiError("badArgument", "GetRecord needs identifier and metadataPrefix")
        self._check_prefix(args["metadataPrefix"])
        record = await self._get(args["identifier"])
        node = ET.Element(f"{{{OAI}}}GetRecord")
        node.append(self._record(record, metadata=True))
        return node

    # --- helpers -------------------------------------------------------------------

    def _check_prefix(self, prefix: str) -> None:
        if prefix != PREFIX:
            raise OaiError("cannotDisseminateFormat", f"the only metadata format is {PREFIX}")

    async def _get(self, identifier: str) -> FeedRecord:
        if not identifier.startswith(self._prefix):
            raise OaiError("idDoesNotExist", f"unknown identifier: {identifier}")
        record = await self._feed.record(identifier.removeprefix(self._prefix))
        if record is None:
            raise OaiError("idDoesNotExist", f"unknown identifier: {identifier}")
        return record

    async def _list(self, args: Mapping[str, str], *, records: bool) -> ET.Element:
        if "set" in args:
            raise OaiError("noSetHierarchy", "this repository has no sets")
        if "resumptionToken" in args:
            after, since, until, total = _untoken(args["resumptionToken"])
            count = False
        else:
            if "metadataPrefix" not in args:
                raise OaiError("badArgument", "metadataPrefix is required")
            self._check_prefix(args["metadataPrefix"])
            after, since, until, total = 0, args.get("from"), args.get("until"), 0
            count = True
            if since and until and len(since) != len(until):
                raise OaiError("badArgument", "from and until have different granularities")
        start = _parse_date(since, end=False) if since else None
        end = _parse_date(until, end=True) if until else None
        if start and end and start > end:
            raise OaiError("badArgument", "from is after until")
        page = await self._feed.page(
            after=after or None, size=PAGE, since=start, until=end, count=count
        )
        if count:
            total = page.total
        if not page.records:
            raise OaiError("noRecordsMatch", "no records match the request")
        node = ET.Element(f"{{{OAI}}}{'ListRecords' if records else 'ListIdentifiers'}")
        for record in page.records:
            if records:
                node.append(self._record(record, metadata=True))
            else:
                node.append(self._header(record))
        token = ET.SubElement(node, f"{{{OAI}}}resumptionToken", {"completeListSize": str(total)})
        if page.next_after is not None:
            token.text = _token(page.next_after, since, until, total)
        elif "resumptionToken" not in args:
            node.remove(token)  # a complete list in one response needs no token
        return node

    def _header(self, record: FeedRecord) -> ET.Element:
        header = ET.Element(f"{{{OAI}}}header")
        ET.SubElement(header, f"{{{OAI}}}identifier").text = self._prefix + record.public_id
        ET.SubElement(header, f"{{{OAI}}}datestamp").text = _stamp(record.modified)
        return header

    def _record(self, record: FeedRecord, *, metadata: bool) -> ET.Element:
        from fdpindex.catalog.feed import record_graph

        node = ET.Element(f"{{{OAI}}}record")
        node.append(self._header(record))
        if metadata:
            graph: Graph = record_graph(
                self._feed,
                record,
                catalog_iri=self._catalog_iri,
                iri=self._record_iri(record.public_id),
            )
            rdf = graph.serialize(format="xml", encoding="utf-8")
            meta = ET.SubElement(node, f"{{{OAI}}}metadata")
            meta.append(ET.fromstring(rdf))  # noqa: S314 - our own serialisation
        return node
