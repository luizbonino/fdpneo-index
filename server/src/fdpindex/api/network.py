"""Network profile API (TASKS 15.9).

* ``GET /index/network-profile``: what this index expects of its FDPs, public:
  the YAML as an administrator wrote it, and the profile it parses to.
* ``PUT /index/network-profile`` (administrators): a new profile as YAML.
* ``DELETE /index/network-profile`` (administrators): back to the installed
  profile (``FDPINDEX_NETWORK_PROFILE``) or the generic one.
* ``GET /index/network-profile/schema``: the template's JSON Schema.
* ``GET /index/network-profile/examples``: the bundled profiles (generic,
  ERDERA).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.auth import Principal, require_admin
from fdpindex.errors import BadRequest
from fdpindex.network import EXAMPLES, NetworkProfile, ProfileError, example, parse

if TYPE_CHECKING:
    from fdpindex.network import NetworkProfileService

router = APIRouter(tags=["Network profile"])
Admin = Annotated[Principal, Depends(require_admin)]


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class NetworkProfileView(_Camel):
    source: str = Field(description="The profile as YAML, as it was written")
    profile: NetworkProfile
    is_default: bool = Field(
        alias="isDefault", description="The installed or generic profile, not an edit"
    )
    empty: bool = Field(description="It expects nothing beyond the FDP specification")


class NetworkProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="The profile as YAML")


class ProfileExample(_Camel):
    name: str
    title: str
    source: str


def _service(request: Request) -> NetworkProfileService:
    return request.app.state.network_profile


async def _view(service: NetworkProfileService) -> NetworkProfileView:
    source, profile, is_default = await service.get()
    return NetworkProfileView(
        source=source, profile=profile, isDefault=is_default, empty=profile.empty
    )


@router.get(
    "/index/network-profile",
    response_model=NetworkProfileView,
    summary="What this index expects of the FDPs it lists",
)
async def get_profile(request: Request) -> NetworkProfileView:
    return await _view(_service(request))


@router.put(
    "/index/network-profile",
    response_model=NetworkProfileView,
    summary="Set the network profile (administrators)",
    responses={400: {"description": "Not a valid profile; the message says why"}},
)
async def put_profile(
    body: NetworkProfileUpdate, request: Request, _admin: Admin
) -> NetworkProfileView:
    """The health reports are computed again, and the network's SHACL checked."""
    try:
        await _service(request).update(body.source)
    except ProfileError as err:
        raise BadRequest(str(err)) from err
    return await _view(_service(request))


@router.delete(
    "/index/network-profile",
    response_model=NetworkProfileView,
    summary="Return to the installed or generic profile (administrators)",
)
async def reset_profile(request: Request, _admin: Admin) -> NetworkProfileView:
    await _service(request).reset()
    return await _view(_service(request))


@router.get("/index/network-profile/schema", summary="The network profile template's JSON Schema")
async def profile_schema() -> dict[str, Any]:
    return NetworkProfile.model_json_schema()


@router.get(
    "/index/network-profile/examples",
    response_model=list[ProfileExample],
    summary="The bundled network profiles",
)
async def profile_examples() -> list[ProfileExample]:
    out: list[ProfileExample] = []
    for name in EXAMPLES:
        source = example(name)
        out.append(
            ProfileExample(name=name, title=parse(source).network.name or name, source=source)
        )
    return out
