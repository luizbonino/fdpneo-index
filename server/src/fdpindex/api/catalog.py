"""The catalogue feed over HTTP (TASKS 11.5; conformance design §6).

* ``GET /index/catalog``: the harvested metadata as a DCAT-AP catalogue of
  catalogue records, paged with Hydra (``after``, ``size``), in Turtle,
  RDF/XML, JSON-LD or N-Triples.
* ``GET /index/catalog/records/{id}``: one catalogue record with its
  resource's triples.
* ``GET``/``POST /index/oai``: the same records over OAI-PMH 2.0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Header, Query, Request, Response

from fdpindex.api.routes import _negotiate  # pyright: ignore[reportPrivateUsage]
from fdpindex.catalog.feed import page_graph, record_graph
from fdpindex.catalog.oai import OaiRepository
from fdpindex.errors import NotFound

if TYPE_CHECKING:
    from fdpindex.catalog.feed import CatalogFeed

router = APIRouter(tags=["Catalogue feed"])

FORMATS = {
    "text/turtle": "turtle",
    "application/ld+json": "json-ld",
    "application/n-triples": "nt",
    "application/rdf+xml": "xml",
}


def _feed(request: Request) -> CatalogFeed:
    feed: CatalogFeed | None = request.app.state.catalog_feed
    if feed is None:
        raise NotFound("The catalogue feed is not enabled on this index (it needs search)")
    return feed


def _base(request: Request) -> str:
    configured: str | None = request.app.state.config.public_url
    return (configured or str(request.base_url)).rstrip("/")


def _urls(request: Request) -> tuple[str, str]:
    base = _base(request)
    return f"{base}/index/catalog", f"{base}/index/catalog/records/"


@router.get(
    "/index/catalog",
    summary="The catalogue feed",
    description="Harvested catalogues, datasets, series and services as a DCAT-AP "
    "catalogue: one dcat:CatalogRecord per resource (foaf:primaryTopic the resource, "
    "dct:source its FDP document, dct:conformsTo the profiles it meets) with the "
    "resource's triples as harvested. Paged with Hydra: follow hydra:next.",
    responses={200: {"content": {media: {} for media in FORMATS}}},
)
async def catalog(
    request: Request,
    after: Annotated[int | None, Query(ge=0, description="Position from hydra:next")] = None,
    size: Annotated[int, Query(ge=1, le=500)] = 100,
    format: Annotated[str | None, Query(description="ttl, rdf, jsonld or nt")] = None,
    accept: Annotated[str, Header()] = "text/turtle",
) -> Response:
    media = _negotiate(format, accept)
    feed = _feed(request)
    page = await feed.page(after=after, size=size)
    catalog_iri, records = _urls(request)

    def link(position: int | None) -> str:
        params: dict[str, str | int] = {"size": size}
        if position is not None:
            params["after"] = position
        if format:
            params["format"] = format
        return f"{catalog_iri}?{urlencode(params)}"

    graph = page_graph(
        feed,
        page,
        catalog_iri=catalog_iri,
        record_iri=lambda public_id: records + public_id,
        view_iri=link(after),
        first_iri=link(None),
        next_iri=link(page.next_after) if page.next_after is not None else None,
    )
    body = graph.serialize(format=FORMATS[media])
    headers = {"Vary": "Accept"}
    if page.next_after is not None:
        headers["Link"] = f'<{link(page.next_after)}>; rel="next"'
    return Response(body, media_type=media, headers=headers)


@router.get(
    "/index/catalog/records/{public_id}",
    summary="A catalogue record",
    description="One dcat:CatalogRecord of the feed with its resource's triples.",
    responses={200: {"content": {media: {} for media in FORMATS}}},
)
async def catalog_record(
    request: Request,
    public_id: str,
    format: Annotated[str | None, Query(description="ttl, rdf, jsonld or nt")] = None,
    accept: Annotated[str, Header()] = "text/turtle",
) -> Response:
    media = _negotiate(format, accept)
    feed = _feed(request)
    record = await feed.record(public_id[:64])
    if record is None:
        raise NotFound(f"No catalogue record '{public_id}'")
    catalog_iri, records = _urls(request)
    graph = record_graph(feed, record, catalog_iri=catalog_iri, iri=records + record.public_id)
    return Response(
        graph.serialize(format=FORMATS[media]), media_type=media, headers={"Vary": "Accept"}
    )


OAI_DESCRIPTION = "The catalogue feed over OAI-PMH 2.0, metadata format dcat_ap (RDF/XML)."


@router.get(
    "/index/oai",
    summary="OAI-PMH",
    description=OAI_DESCRIPTION,
    responses={200: {"content": {"text/xml": {}}}},
)
async def oai_get(request: Request) -> Response:
    return await _oai(request, dict(request.query_params))


@router.post(
    "/index/oai",
    summary="OAI-PMH (form)",
    description=OAI_DESCRIPTION,
    responses={200: {"content": {"text/xml": {}}}},
)
async def oai_post(request: Request) -> Response:
    form = await request.form()
    return await _oai(request, {k: str(v) for k, v in form.items()})


async def _oai(request: Request, args: dict[str, str]) -> Response:
    feed = _feed(request)
    base = _base(request)
    catalog_iri, records = _urls(request)
    repository = OaiRepository(
        feed,
        base_url=f"{base}/index/oai",
        host=urlsplit(base).hostname or "localhost",
        catalog_iri=catalog_iri,
        record_iri=lambda public_id: records + public_id,
        admin_email=request.app.state.config.catalog_contact_email,
    )
    return Response(await repository.respond(args), media_type="text/xml; charset=utf-8")
