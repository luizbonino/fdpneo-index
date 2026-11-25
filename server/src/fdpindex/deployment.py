"""Deployment profiles: bundles of defaults for a kind of index (TASKS 11.6).

``FDPINDEX_DEPLOYMENT_PROFILE`` picks one. A profile sets defaults only:
everything stays an administrator's setting, and "reset to defaults" returns
to the profile's values.

* ``default``: DCAT-AP 3 checked, everything shown, the bundled search
  profile.
* ``ehds``: the European Health Data Space (conformance design §7):
  HealthDCAT-AP and DCAT-AP 3 checked, in that order (the catalogue feed
  names the first profile a record meets); the catalogue feed republishes
  only what conforms to HealthDCAT-AP; the search starts from the
  HealthDCAT-AP extension (health categories, numbers of individuals,
  quality certificates …). The EU vocabularies' labels are bundled already.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import yaml

from fdpindex.conformance.settings import ActivePack, ConformanceSettings, Surfaces
from fdpindex.search.profile_store import examples

DeploymentName = Literal["default", "ehds"]


@dataclass(frozen=True)
class DeploymentProfile:
    name: DeploymentName
    conformance: ConformanceSettings
    search_example: str | None = None

    def search_overlay(self) -> dict[str, Any] | None:
        if self.search_example is None:
            return None
        for example in examples():
            if example.name == self.search_example:
                loaded = yaml.safe_load(example.text)
                return loaded if isinstance(loaded, dict) else None
        raise ValueError(f"no bundled search example '{self.search_example}'")


PROFILES: dict[DeploymentName, DeploymentProfile] = {
    "default": DeploymentProfile("default", ConformanceSettings()),
    "ehds": DeploymentProfile(
        "ehds",
        ConformanceSettings(
            packs=[
                ActivePack(key="healthdcat-ap@release-8", role="required"),
                ActivePack(key="dcat-ap@3.0.1", role="required"),
            ],
            surfaces=Surfaces(feed="healthdcat-ap"),
        ),
        search_example="healthdcat-ap",
    ),
}


def deployment_profile(name: DeploymentName) -> DeploymentProfile:
    return PROFILES[name]
