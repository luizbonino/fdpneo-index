"""Conformance API (Phase 11; conformance design §8).

``GET /index/conformance/packs`` lists the SHACL profile packs this index
has, bundled and the operator's. Validation results and reports (11.3) and
the deployment's choice of packs follow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.conformance.packs import PackFile, Role
from fdpindex.search.profile import Kind

if TYPE_CHECKING:
    from fdpindex.conformance.packs import Pack

router = APIRouter(tags=["Conformance"])


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


@router.get(
    "/index/conformance/packs",
    summary="SHACL profile packs",
    description="The packs this index can validate harvested records against: bundled "
    "(DCAT-AP, HealthDCAT-AP, Health-RI core) and the operator's.",
)
async def list_packs(request: Request) -> list[PackView]:
    packs: dict[str, Pack] = request.app.state.conformance_packs
    return [pack_view(p) for _, p in sorted(packs.items())]
