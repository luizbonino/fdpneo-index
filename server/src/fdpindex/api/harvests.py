"""Harvest provenance over HTTP (TASKS 15.3, design note provenance).

* ``GET /index/harvests/{id}``: one harvest (a walk over an FDP's metadata)
  with its runs. The URL is the harvest's IRI: Turtle, JSON-LD, N-Triples or
  RDF/XML (``format`` or ``Accept``) in PROV-O and FDP-O 2.1, JSON for the
  client, and a browser is sent to the harvest's page (303).
* ``GET /index/entries/{uuid}/harvests``: an FDP's harvests, the latest first.

Public for FDPs in the public list (decision 3): times, outcome, counts and
failure reasons in words.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from rdflib import Graph

from fdpindex.api.routes import (  # pyright: ignore[reportPrivateUsage]
    RDF_TYPES,
    Caller,
    Index,
    _negotiate,
    _visible,
)
from fdpindex.errors import NotFound
from fdpindex.provenance import bind, describe_harvest, describe_index, record_view

router = APIRouter(tags=["Harvest provenance"])


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class HarvestRecordRunView(_Camel):
    run: int
    started_at: str | None = Field(alias="startedAt")
    ended_at: str | None = Field(alias="endedAt")
    pages_read: int = Field(alias="pagesRead")
    added: int
    changed: int
    removed: int
    not_modified: int = Field(alias="notModified")
    failed: int


class HarvestRecordView(_Camel):
    id: str
    iri: str = Field(description="The harvest's IRI, this resource's URL")
    entry: str
    client_url: str = Field(alias="clientUrl")
    title: str | None
    started_at: str = Field(alias="startedAt")
    ended_at: str | None = Field(alias="endedAt")
    outcome: Literal["in progress", "complete", "partial", "failed"]
    pages_read: int = Field(alias="pagesRead")
    added: int
    changed: int
    removed: int
    not_modified: int = Field(alias="notModified")
    failed: int
    at_ceiling: bool = Field(alias="atCeiling", description="Stopped at the page limit")
    failure_reason: str | None = Field(alias="failureReason")
    runs: list[HarvestRecordRunView]


class HarvestRecordPage(_Camel):
    items: list[HarvestRecordView]
    total: int
    page: int
    size: int


def _base(request: Request) -> str:
    configured: str | None = request.app.state.config.public_url
    return (configured or str(request.base_url)).rstrip("/")


@router.get(
    "/index/harvests/{harvest_id}",
    response_model=HarvestRecordView,
    summary="One harvest of an FDP, with its runs",
    responses={
        200: {"content": {"application/json": {}, **{m: {} for m in RDF_TYPES}}},
        303: {"description": "A browser: the harvest's page"},
    },
)
async def harvest(
    harvest_id: UUID,
    request: Request,
    index: Index,
    caller: Caller,
    format: Annotated[str | None, Query(description="ttl, jsonld, nt or rdf")] = None,
    accept: Annotated[str, Header()] = "application/json",
) -> Response:
    """When the index walked the FDP, how it ended, and the pages it read, added,
    changed, removed, found unchanged and could not read, per run too."""
    record = await index.harvest_record(harvest_id)
    try:
        await _visible(index, caller, record.entry_uuid)
    except NotFound as err:
        raise NotFound("Harvest not found") from err
    entry = await index.get_entry(record.entry_uuid)
    base = _base(request)
    wants_rdf = bool(format) or any(m in accept for m in RDF_TYPES)
    if not wants_rdf and "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(f"{base}/harvests/{harvest_id}", status_code=303)
    if wants_rdf:
        media = _negotiate(format, accept)
        graph = bind(Graph())
        describe_index(graph, base, request.app.state.config.catalog_title)
        describe_harvest(graph, record, fdp=entry.client_url, base=base)
        return Response(
            graph.serialize(format=RDF_TYPES[media]), media_type=media, headers={"Vary": "Accept"}
        )
    view = HarvestRecordView.model_validate(
        {
            **record_view(record),
            "iri": f"{base}/index/harvests/{harvest_id}",
            "clientUrl": entry.client_url,
            "title": (entry.repository_metadata or {}).get("title"),
        }
    )
    return Response(
        view.model_dump_json(by_alias=True),
        media_type="application/json",
        headers={"Vary": "Accept"},
    )


@router.get(
    "/index/entries/{entry_id}/harvests",
    response_model=HarvestRecordPage,
    summary="An FDP's harvests, the latest first",
)
async def entry_harvests(
    entry_id: UUID,
    request: Request,
    index: Index,
    caller: Caller,
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HarvestRecordPage:
    await _visible(index, caller, entry_id)
    entry = await index.get_entry(entry_id)
    records, total = await index.harvest_records(entry_id, page=page, size=size)
    base = _base(request)
    title = (entry.repository_metadata or {}).get("title")
    return HarvestRecordPage.model_validate(
        {
            "items": [
                {
                    **record_view(r),
                    "iri": f"{base}/index/harvests/{r.id}",
                    "clientUrl": entry.client_url,
                    "title": title,
                }
                for r in records
            ],
            "total": total,
            "page": page,
            "size": size,
        }
    )
