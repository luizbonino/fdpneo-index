"""Conformance API (Phase 11; conformance design §8).

* ``GET /index/conformance/packs``: the SHACL profile packs this index has,
  bundled and the operator's.
* ``GET``/``PUT``/``DELETE /index/conformance/settings`` (admin): the packs
  validated against and who may read reports.
* ``GET /index/conformance/entries/{uuid}``: an FDP's report.
* ``GET /index/conformance/records``: records with their status, paged.
* ``GET /index/conformance/records/{publicId}``: one record's findings.
* ``POST /index/conformance/validate`` (admin): validate again.

Reports are public by default, like the metadata they describe; the
``reports`` setting makes them admin-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.auth import Principal, current_principal, require_admin
from fdpindex.conformance.packs import PackFile, Role
from fdpindex.conformance.reports import (
    EntryReport,
    RecordPage,
    RecordReport,
    entry_report,
    record_page,
    record_report,
)
from fdpindex.conformance.settings import ConformanceSettings, ConformanceSettingsView
from fdpindex.errors import BadRequest, Forbidden, NotFound, Unauthorized
from fdpindex.models import IndexEntry
from fdpindex.search.profile import Kind

if TYPE_CHECKING:
    from fdpindex.conformance.packs import Pack
    from fdpindex.conformance.service import ConformanceService
    from fdpindex.conformance.settings import ConformanceSettingsService

router = APIRouter(tags=["Conformance"])

Caller = Annotated[Principal, Depends(current_principal)]
Admin = Annotated[Principal, Depends(require_admin)]


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class PackFileView(_Camel):
    path: str
    role: Role
    variant: str | None
    enabled: bool = Field(description="Validated against by default")
    note: str


class PackView(_Camel):
    key: str = Field(description="id@version")
    id: str
    version: str
    title: dict[str, str]
    description: dict[str, str]
    publisher: str
    licence: str
    specification: str = Field(description="The specification's IRI (dct:conformsTo)")
    source: str | None = Field(description="Where the shapes come from")
    applies_to: list[Kind] = Field(alias="appliesTo")
    variants: list[str] = Field(description="Shapes chosen per record, by `variantProperty`")
    variant_property: str | None = Field(alias="variantProperty")
    bundled: bool
    files: list[PackFileView]


class ValidateRequest(_Camel):
    entry: UUID | None = Field(default=None, description="One FDP; all when absent")


class ValidateAccepted(_Camel):
    scheduled: str = Field(description="`all` or the FDP's uuid")


def _file(file: PackFile) -> PackFileView:
    return PackFileView(
        path=file.path, role=file.role, variant=file.variant, enabled=file.enabled, note=file.note
    )


def pack_view(pack: Pack) -> PackView:
    m = pack.manifest
    return PackView(
        key=pack.key,
        id=m.id,
        version=m.version,
        title=m.title,
        description=m.description,
        publisher=m.publisher,
        licence=m.licence,
        specification=m.specification,
        source=m.source.url if m.source else None,
        appliesTo=list(m.applies_to),
        variants=list(m.variants.values) if m.variants else [],
        variantProperty=m.variants.property if m.variants else None,
        bundled=pack.bundled,
        files=[_file(f) for f in m.files],
    )


def _settings(request: Request) -> ConformanceSettingsService:
    return request.app.state.conformance_settings


def _service(request: Request) -> ConformanceService:
    service: ConformanceService | None = request.app.state.conformance
    if service is None:
        raise NotFound("Conformance validation is not enabled on this index")
    return service


async def _readable(request: Request, caller: Principal) -> ConformanceService:
    """The service, if the caller may read reports (the ``reports`` setting)."""
    service = _service(request)
    if (await service.settings.get()).reports == "admin" and not caller.is_admin:
        if caller.anonymous:
            raise Unauthorized("Conformance reports are for administrators")
        raise Forbidden("Conformance reports are for administrators")
    return service


async def _active(service: ConformanceService) -> list[tuple[str, str]]:
    settings = await service.settings.get()
    packs = service.settings.packs
    return [(packs[a.key].id, packs[a.key].version) for a in settings.packs if a.key in packs]


@router.get(
    "/index/conformance/packs",
    summary="SHACL profile packs",
    description="The packs this index can validate harvested records against: bundled "
    "(DCAT-AP, HealthDCAT-AP, Health-RI core) and the operator's.",
)
async def list_packs(request: Request) -> list[PackView]:
    packs: dict[str, Pack] = request.app.state.conformance_packs
    return [pack_view(p) for _, p in sorted(packs.items())]


@router.get(
    "/index/conformance/settings",
    summary="Conformance settings",
    description="The packs validated against, in order, and who may read reports.",
)
async def get_settings(request: Request, _admin: Admin) -> ConformanceSettingsView:
    return await _settings(request).view()


@router.put(
    "/index/conformance/settings",
    summary="Change the conformance settings",
    description="Records are validated again in the background where a pack, its files or "
    "its kinds changed; results of packs no longer listed are removed.",
)
async def put_settings(
    request: Request, body: ConformanceSettings, _admin: Admin
) -> ConformanceSettingsView:
    try:
        view = await _settings(request).update(body)
    except ValueError as err:
        raise BadRequest(str(err)) from err
    service: ConformanceService | None = request.app.state.conformance
    if service is not None:
        service.settings_changed()
    return view


@router.delete(
    "/index/conformance/settings",
    summary="Reset the conformance settings",
    description="DCAT-AP 3.0.1 only, reports public.",
)
async def reset_settings(request: Request, _admin: Admin) -> ConformanceSettingsView:
    view = await _settings(request).reset()
    service: ConformanceService | None = request.app.state.conformance
    if service is not None:
        service.settings_changed()
    return view


@router.post(
    "/index/conformance/validate",
    status_code=202,
    summary="Validate again",
    description="Validate every record of one FDP, or of all, again, even if nothing changed.",
)
async def validate(request: Request, body: ValidateRequest, _admin: Admin) -> ValidateAccepted:
    service = _service(request)
    service.revalidate(body.entry)
    return ValidateAccepted(scheduled=str(body.entry) if body.entry else "all")


@router.get(
    "/index/conformance/entries/{entry_id}",
    summary="An FDP's conformance report",
    description="Per active pack, the FDP's records by status, and the most frequent "
    "problems with an example record.",
)
async def get_entry_report(request: Request, entry_id: UUID, caller: Caller) -> EntryReport:
    service = await _readable(request, caller)
    async with request.app.state.session_factory() as session:
        if await session.get(IndexEntry, entry_id) is None:
            raise NotFound(f"Entry '{entry_id}' not found")
        return await entry_report(session, entry_id, await _active(service))


@router.get(
    "/index/conformance/records",
    summary="Records and their conformance",
    description="Records validated against one pack, most violations first. `path`, "
    "`component` and `severity` keep the records with that problem.",
)
async def list_records(
    request: Request,
    caller: Caller,
    pack: Annotated[
        str | None, Query(description="A pack id; the first active pack by default")
    ] = None,
    entry: UUID | None = None,
    status: Literal["conforms", "violations", "error"] | None = None,
    path: Annotated[str | None, Query(max_length=2048)] = None,
    component: Annotated[str | None, Query(max_length=128)] = None,
    severity: Literal["violation", "warning", "info"] | None = None,
    page: Annotated[int, Query(ge=0, le=10_000)] = 0,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RecordPage:
    service = await _readable(request, caller)
    active = await _active(service)
    chosen = pack or (active[0][0] if active else None)
    if chosen is None:
        raise BadRequest("No pack is active")
    async with request.app.state.session_factory() as session:
        return await record_page(
            session,
            entry_id=entry,
            pack=chosen,
            status=status,
            path=path,
            component=component,
            severity=severity,
            offset=page * size,
            limit=size,
        )


@router.get(
    "/index/conformance/records/{public_id}",
    summary="A record's findings",
    description="One record's results per pack, with up to 50 findings each. The id is the "
    "search's resource id.",
)
async def get_record(request: Request, public_id: str, caller: Caller) -> RecordReport:
    service = await _readable(request, caller)
    order = [p for p, _ in await _active(service)]
    async with request.app.state.session_factory() as session:
        report = await record_report(session, public_id[:64], order)
    if report is None:
        raise NotFound(f"No conformance results for '{public_id}'")
    return report
