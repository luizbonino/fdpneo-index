"""SHACL profile packs and record validation (TASKS 11.2)."""

from __future__ import annotations

import gzip
import textwrap
from typing import TYPE_CHECKING

import pytest
from rdflib import Graph, Namespace, URIRef

from fdpindex.conformance.packs import (
    PackError,
    discover,
    load_pack,
    sums,
    verify,
)
from fdpindex.conformance.validator import SH, ShaclValidator, repair
from fdpindex.conformance.vocabularies import VocabularySet, bundled, extract

if TYPE_CHECKING:
    from pathlib import Path

    from fdpindex.conformance.packs import Pack

    Packs = dict[str, Pack]

pytestmark = pytest.mark.unit

EU = "http://publications.europa.eu/resource/authority/"
DS = "https://fdp.example.org/dataset/1"
PREFIXES = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix vcard: <http://www.w3.org/2006/vcard/ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
"""


def graph(turtle: str) -> Graph:
    g = Graph()
    g.parse(data=PREFIXES + textwrap.dedent(turtle), format="turtle")
    return g


def dataset(extra: str = "") -> Graph:
    """A DCAT-AP 3 dataset with every mandatory property, its values from the EU tables."""
    return graph(f"""
        <{DS}> a dcat:Dataset ;
            dct:title "Air quality"@en ;
            dct:description "Hourly air quality measurements."@en ;
            dct:accessRights <{EU}access-right/PUBLIC> ;
            dct:language <{EU}language/ENG> ;
            dcat:theme <{EU}data-theme/ENVI> ;
            dct:publisher <https://org.example.org/> ;
            dcat:contactPoint [ a vcard:Kind ; vcard:fn "Data office" ;
                                vcard:hasEmail <mailto:data@example.org> ] .
        <https://org.example.org/> a foaf:Agent ; foaf:name "Example agency" .
        {extra}
    """)


@pytest.fixture(scope="module")
def packs() -> Packs:
    return discover()


@pytest.fixture(scope="module")
def validator() -> ShaclValidator:
    return ShaclValidator()


def test_bundled_packs_match_their_sources(packs: Packs) -> None:
    assert set(packs) == {"dcat-ap@3.0.1", "healthdcat-ap@release-8", "health-ri-core@2.0.3"}
    for pack in packs.values():
        assert pack.bundled
        assert verify(pack) == []
        assert pack.manifest.licence == "https://creativecommons.org/licenses/by/4.0/"
        assert pack.manifest.source is not None


def test_files_by_variant_and_defaults(packs: Packs) -> None:
    health = packs["healthdcat-ap@release-8"]
    restricted = [f.path for f in health.files_for("restricted")]
    assert "shapes/restricted-shapes.ttl" in restricted
    assert "shapes/public-shapes.ttl" not in restricted
    assert "catalogue/shapes.ttl" in restricted  # shared by every variant
    assert "ranges/range.ttl" not in restricted  # off by default
    assert [f.path for f in health.files_for("public", ["ranges/range.ttl"])] == [
        "ranges/range.ttl"
    ]


def test_variant_follows_access_rights(packs: Packs) -> None:
    health = packs["healthdcat-ap@release-8"]
    assert health.variant_for(dataset(), DS) == "public"
    restricted = graph(
        f"<{DS}> <http://purl.org/dc/terms/accessRights> <{EU}access-right/RESTRICTED> ."
    )
    assert health.variant_for(restricted, DS) == "restricted"
    assert health.variant_for(restricted, None) == "restricted"
    assert (
        health.variant_for(graph(f"<{DS}> a <http://www.w3.org/ns/dcat#Dataset> ."), DS) == "public"
    )
    assert packs["dcat-ap@3.0.1"].variant_for(restricted, DS) is None


def test_vocabulary_checks_pass_on_eu_values(packs: Packs, validator: ShaclValidator) -> None:
    outcome = validator.validate(dataset(), packs["dcat-ap@3.0.1"], root=DS)
    assert outcome.status == "conforms", outcome.findings
    assert outcome.violations == 0
    # Recommended properties are warnings, not counted against conformance.
    assert outcome.warnings > 0
    assert {f.severity for f in outcome.findings} == {"warning"}
    # Vocabulary concepts added for the checks are never judged themselves.
    assert all(f.focus_node in {DS, "https://org.example.org/"} for f in outcome.findings)


def test_values_outside_the_eu_tables(packs: Packs, validator: ShaclValidator) -> None:
    pack = packs["dcat-ap@3.0.1"]
    frequency = URIRef("http://purl.org/dc/terms/accrualPeriodicity")
    access = URIRef("http://purl.org/dc/terms/accessRights")
    record = dataset()
    record.add((URIRef(DS), frequency, URIRef(f"{EU}frequency/ANNUAL")))
    assert validator.validate(record, pack, root=DS).status == "conforms"
    # A frequency outside the EU table is a violation in DCAT-AP 3.0.1 ...
    record.remove((URIRef(DS), frequency, None))
    record.add((URIRef(DS), frequency, URIRef("https://terms.example.org/yearly")))
    outcome = validator.validate(record, pack, root=DS)
    assert outcome.status == "violations"
    assert str(frequency) in {f.path for f in outcome.findings if f.severity == "violation"}
    # ... an access right outside it only a warning.
    record = dataset()
    record.remove((URIRef(DS), access, None))
    record.add((URIRef(DS), access, URIRef("https://rights.example.org/open")))
    outcome = validator.validate(record, pack, root=DS)
    assert outcome.status == "conforms"
    assert str(access) in {f.path for f in outcome.findings if f.severity == "warning"}


def test_missing_mandatory_property(packs: Packs, validator: ShaclValidator) -> None:
    record = dataset()
    record.remove((URIRef(DS), URIRef("http://purl.org/dc/terms/description"), None))
    outcome = validator.validate(record, packs["dcat-ap@3.0.1"], root=DS)
    assert outcome.status == "violations"
    finding = next(f for f in outcome.findings if f.severity == "violation")
    assert finding.focus_node == DS
    assert finding.path == "http://purl.org/dc/terms/description"
    assert finding.component == "MinCountConstraintComponent"
    assert finding.shape is not None and finding.shape.endswith("Dataset_Shape")


@pytest.mark.parametrize("pack", ["healthdcat-ap@release-8", "health-ri-core@2.0.3"])
def test_health_packs_load_and_judge(packs: Packs, validator: ShaclValidator, pack: str) -> None:
    outcome = validator.validate(dataset(), packs[pack], root=DS)
    assert outcome.status == "violations"  # a plain DCAT-AP dataset lacks the health properties
    assert outcome.error is None
    assert len(outcome.findings) <= 50


def test_repair_turns_pathless_property_shapes_into_node_shapes() -> None:
    shapes = graph("""
        <urn:s> a sh:NodeShape ; sh:targetClass dcat:Dataset ;
            sh:property [ sh:or ( [ sh:path dct:title ; sh:minCount 1 ]
                                  [ sh:path dct:description ; sh:minCount 1 ] ) ] ,
                        [ sh:path dct:issued ; sh:maxCount 1 ] .
    """)
    assert repair(shapes) == 1
    assert len(list(shapes.objects(URIRef("urn:s"), SH.node))) == 1
    assert len(list(shapes.objects(URIRef("urn:s"), SH.property))) == 1


def _operator_pack(root: Path, pack_id: str = "local", version: str = "1") -> Path:
    folder = root / pack_id / version
    folder.mkdir(parents=True)
    (folder / "pack.yaml").write_text(
        textwrap.dedent(f"""
            id: {pack_id}
            version: "{version}"
            title: {{en: Local rules}}
            specification: https://example.org/rules
            appliesTo: [dataset]
            files:
              - {{path: shapes.ttl, role: shapes}}
        """),
        encoding="utf-8",
    )
    (folder / "shapes.ttl").write_text(
        PREFIXES
        + """
        <urn:local#Dataset> a sh:NodeShape ; sh:targetClass dcat:Dataset ;
            sh:property [ sh:path dct:issued ; sh:minCount 1 ; sh:severity sh:Violation ] .
        """,
        encoding="utf-8",
    )
    (folder / "SHA256SUMS").write_text(sums(folder, ["shapes.ttl"]), encoding="utf-8")
    return folder


def test_operator_packs(tmp_path: Path, validator: ShaclValidator) -> None:
    _operator_pack(tmp_path)
    packs = discover(tmp_path)
    local = packs["local@1"]
    assert not local.bundled
    assert verify(local) == []
    outcome = validator.validate(dataset(), local, root=DS)
    assert outcome.violations == 1
    assert outcome.findings[0].path == "http://purl.org/dc/terms/issued"
    (local.directory / "shapes.ttl").write_text("# changed\n", encoding="utf-8")
    assert verify(local) == ["local@1: shapes.ttl differs from SHA256SUMS"]


def test_operator_pack_errors(tmp_path: Path) -> None:
    _operator_pack(tmp_path, "dcat-ap", "3.0.1")
    with pytest.raises(PackError, match="exists already"):
        discover(tmp_path)
    folder = _operator_pack(tmp_path / "other", "x", "1")
    (folder.parent / "2").mkdir()
    (folder / "pack.yaml").rename(folder.parent / "2" / "pack.yaml")
    with pytest.raises(PackError, match="<id>/<version>"):
        load_pack(folder.parent / "2")
    with pytest.raises(PackError, match="not a directory"):
        discover(tmp_path / "missing")


def test_manifest_rejects_paths_outside_the_pack(tmp_path: Path) -> None:
    folder = _operator_pack(tmp_path)
    text = (folder / "pack.yaml").read_text(encoding="utf-8")
    (folder / "pack.yaml").write_text(text.replace("shapes.ttl", "../shapes.ttl"), encoding="utf-8")
    with pytest.raises(PackError, match="relative path"):
        load_pack(folder)


def test_a_broken_shapes_file_is_an_error_status(tmp_path: Path) -> None:
    folder = _operator_pack(tmp_path)
    (folder / "shapes.ttl").write_text("this is not turtle", encoding="utf-8")
    outcome = ShaclValidator().validate(dataset(), load_pack(folder), root=DS)
    assert outcome.status == "error"
    assert outcome.error


def test_vocabulary_extract_and_closure(tmp_path: Path) -> None:
    source = graph(f"""
        @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
        <{EU}access-right/PUBLIC> a skos:Concept ; skos:inScheme <{EU}access-right> ;
            skos:prefLabel "public"@en .
        <{EU}access-right> a skos:ConceptScheme .
        <{EU}other> a skos:Concept .
    """)
    keep = [
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "http://www.w3.org/2004/02/skos/core#inScheme",
    ]
    data = extract([source], keep)
    assert data == extract([source], keep)  # reproducible bytes
    assert b"prefLabel" not in gzip.decompress(data)
    path = tmp_path / "v.nt.gz"
    path.write_bytes(data)
    vocabulary = VocabularySet.read(path)
    assert len(vocabulary) == 4
    added = set(vocabulary.closure(dataset()))
    skos = Namespace("http://www.w3.org/2004/02/skos/core#")
    assert (URIRef(f"{EU}access-right/PUBLIC"), skos.inScheme, URIRef(f"{EU}access-right")) in added
    assert any(s == URIRef(f"{EU}access-right") for s, _, _ in added)  # the scheme's type
    assert not any(s == URIRef(f"{EU}other") for s, _, _ in added)


def test_bundled_eu_vocabulary() -> None:
    eu = bundled("eu")
    assert len(eu) > 30_000
    skos = "http://www.w3.org/2004/02/skos/core#inScheme"
    schemes = {str(o) for _, p, o in eu.about[URIRef(f"{EU}language/NLD")] if str(p) == skos}
    assert f"{EU}language" in schemes
