"""Fill a local index with synthetic FAIR Data Points, for trying the search by hand.

Every FDP, publisher and address is fictional and says so: client URLs are
``https://fdp-NNN.synthetic.example``, root titles end in "(synthetic)", DOIs
use DataCite's test prefix ``10.5072``. The metadata is shaped like real FDP
metadata (DCAT, EU authority tables, HealthDCAT-AP record counts), with
readable titles and descriptions, so that searches, facets and SPARQL have
something meaningful to find. A few titles are multilingual and a few words
carry accents, for the stemming and accent-folding paths.

The script writes accepted entries and their harvested nodes, exactly as a
harvest stores them; the server's search indexer and SPARQL sync build the
rest. Restart the server afterwards (start-up reconciles both). The entries
are not checked for liveness (their next check is set far ahead): their
hosts do not exist.

    # inside the server container of the Compose stack:
    python seed_demo.py --fdps 150            # add (or replace) the synthetic FDPs
    python seed_demo.py --remove              # remove them again

The database URL comes from ``FDPINDEX_DATABASE_URL`` unless given.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, RDF, XSD
from sqlalchemy import delete, select

from fdpindex.db import make_engine, make_session_factory
from fdpindex.harvest import serialise
from fdpindex.models import EntryPermit, EntryState, HarvestedNode, IndexEntry
from fdpindex.rdf import extract_repository_metadata

DOMAIN = "synthetic.example"
NAMESPACE = UUID("3b6f0c2e-9a41-4d8e-8f57-1c2d3e4f5a60")
EU = "http://publications.europa.eu/resource/authority/"
FDPO = Namespace("https://w3id.org/fdp/fdp-o#")
R3D = Namespace("http://www.re3data.org/schema/3-0#")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")
HEALTH = Namespace("http://healthdataportal.eu/ns/health#")
LDP = Namespace("http://www.w3.org/ns/ldp#")
SCHEMA = Namespace("http://schema.org/")
TIME = Namespace("http://www.w3.org/2006/time#")
DCAT3 = Namespace("http://www.w3.org/ns/dcat#")  # rdflib's DCAT lacks dcat:version

COUNTRIES = {
    "NL": ("NLD", "nl"),
    "DE": ("DEU", "de"),
    "BE": ("BEL", "nl"),
    "FR": ("FRA", "fr"),
    "ES": ("ESP", "es"),
    "IT": ("ITA", "it"),
    "SE": ("SWE", "sv"),
    "FI": ("FIN", "fi"),
    "DK": ("DNK", "da"),
    "PT": ("PRT", "pt"),
    "CH": ("CHE", "de"),
    "AT": ("AUT", "de"),
}
LANGUAGES = {
    "nl": "NLD",
    "de": "DEU",
    "fr": "FRA",
    "es": "SPA",
    "it": "ITA",
    "sv": "SWE",
    "fi": "FIN",
    "da": "DAN",
    "pt": "POR",
}
INSTITUTION_KINDS = [
    "University Medical Centre",
    "Institute for Public Health",
    "Biobank Consortium",
    "Cancer Registry Foundation",
    "Centre for Environmental Research",
    "Institute of Social Research",
    "Agricultural Research Station",
    "Energy Systems Laboratory",
    "Mobility Data Office",
    "Rare Disease Network",
    "Genomics Centre",
    "Heart Foundation Research Unit",
]
PLACES = [
    "Northfield",
    "Lindenberg",
    "Westmarsh",
    "Ostvik",
    "Valbonne",
    "Riverside",
    "Hollandsweide",
    "Kastelburg",
    "Santa Lúcia",
    "Ålesund Coast",
    "Mönchwald",
    "Brightwater",
    "Saint-Émile",
    "Zürichsee",
    "Torreverde",
    "Eastholm",
    "Groenhaven",
    "Lakeshire",
]


@dataclass(frozen=True)
class Topic:
    name: str
    theme: str
    keywords: tuple[str, ...]
    kinds: tuple[str, ...]
    formats: tuple[str, ...]
    restricted: float  # share of datasets with restricted access
    translations: dict[str, str] = field(default_factory=lambda: {})


TOPICS = [
    Topic(
        "breast cancer",
        "HEAL",
        ("oncology", "breast cancer", "screening", "mammography", "tumour"),
        ("registry", "cohort", "screening programme"),
        ("CSV", "XLSX", "PDF"),
        0.8,
        {"nl": "borstkanker", "de": "Brustkrebs", "fr": "cancer du sein"},
    ),
    Topic(
        "colorectal cancer",
        "HEAL",
        ("oncology", "colorectal cancer", "colonoscopy", "survival"),
        ("registry", "cohort"),
        ("CSV", "PDF"),
        0.8,
        {"nl": "darmkanker", "de": "Darmkrebs", "fr": "cancer colorectal"},
    ),
    Topic(
        "type 2 diabetes",
        "HEAL",
        ("diabetes", "glucose", "HbA1c", "primary care", "metabolism"),
        ("cohort", "registry", "trial"),
        ("CSV", "JSON", "PDF"),
        0.7,
        {"nl": "diabetes type 2", "de": "Typ-2-Diabetes", "fr": "diabète de type 2"},
    ),
    Topic(
        "heart failure",
        "HEAL",
        ("cardiology", "heart failure", "echocardiography", "hospital admissions"),
        ("cohort", "registry", "trial"),
        ("CSV", "DICOM", "PDF"),
        0.75,
        {"nl": "hartfalen", "de": "Herzinsuffizienz", "fr": "insuffisance cardiaque"},
    ),
    Topic(
        "dementia",
        "HEAL",
        ("dementia", "Alzheimer", "cognition", "ageing", "memory clinic"),
        ("cohort", "study"),
        ("CSV", "PDF"),
        0.8,
        {"nl": "dementie", "de": "Demenz", "fr": "démence"},
    ),
    Topic(
        "asthma in children",
        "HEAL",
        ("asthma", "paediatrics", "lung function", "allergy", "air quality"),
        ("cohort", "study"),
        ("CSV", "XLSX"),
        0.6,
        {"nl": "astma bij kinderen", "de": "Asthma bei Kindern", "fr": "asthme chez l'enfant"},
    ),
    Topic(
        "rare metabolic disorders",
        "HEAL",
        ("rare diseases", "metabolism", "newborn screening", "orphan drugs"),
        ("registry",),
        ("CSV", "VCF", "PDF"),
        0.9,
        {
            "nl": "zeldzame stofwisselingsziekten",
            "de": "seltene Stoffwechselerkrankungen",
            "fr": "maladies métaboliques rares",
        },
    ),
    Topic(
        "whole-genome sequencing",
        "HEAL",
        ("genomics", "sequencing", "variants", "biobank", "GWAS"),
        ("cohort", "collection"),
        ("VCF", "FASTQ", "BAM", "CSV"),
        0.95,
        {
            "nl": "volledig-genoomsequencing",
            "de": "Ganzgenomsequenzierung",
            "fr": "séquençage du génome entier",
        },
    ),
    Topic(
        "COVID-19",
        "HEAL",
        ("COVID-19", "SARS-CoV-2", "vaccination", "infection", "intensive care"),
        ("registry", "cohort", "survey"),
        ("CSV", "JSON"),
        0.5,
        {"nl": "COVID-19", "de": "COVID-19", "fr": "COVID-19"},
    ),
    Topic(
        "mental health of adolescents",
        "HEAL",
        ("mental health", "depression", "anxiety", "adolescents", "wellbeing"),
        ("survey", "cohort"),
        ("CSV", "SPSS", "PDF"),
        0.7,
        {
            "nl": "mentale gezondheid van jongeren",
            "de": "psychische Gesundheit Jugendlicher",
            "fr": "santé mentale des adolescents",
        },
    ),
    Topic(
        "air quality",
        "ENVI",
        ("air quality", "particulate matter", "NO2", "monitoring stations", "pollution"),
        ("measurements", "monitoring network"),
        ("CSV", "NetCDF", "JSON"),
        0.05,
        {"nl": "luchtkwaliteit", "de": "Luftqualität", "fr": "qualité de l'air"},
    ),
    Topic(
        "river water quality",
        "ENVI",
        ("water quality", "rivers", "nitrates", "sampling", "ecology"),
        ("measurements", "monitoring network"),
        ("CSV", "NetCDF"),
        0.05,
        {
            "nl": "waterkwaliteit van rivieren",
            "de": "Wasserqualität der Flüsse",
            "fr": "qualité de l'eau des rivières",
        },
    ),
    Topic(
        "urban heat islands",
        "ENVI",
        ("climate", "temperature", "urban areas", "satellite", "heat"),
        ("measurements", "model output"),
        ("NetCDF", "GeoTIFF", "CSV"),
        0.0,
        {
            "nl": "stedelijke hitte-eilanden",
            "de": "städtische Wärmeinseln",
            "fr": "îlots de chaleur urbains",
        },
    ),
    Topic(
        "household income",
        "SOCI",
        ("income", "households", "poverty", "census", "inequality"),
        ("survey", "panel"),
        ("CSV", "SPSS", "XLSX"),
        0.4,
        {"nl": "huishoudinkomen", "de": "Haushaltseinkommen", "fr": "revenu des ménages"},
    ),
    Topic(
        "labour market participation",
        "ECON",
        ("employment", "labour market", "unemployment", "education level"),
        ("panel", "survey"),
        ("CSV", "XLSX"),
        0.3,
        {
            "nl": "arbeidsparticipatie",
            "de": "Erwerbsbeteiligung",
            "fr": "participation au marché du travail",
        },
    ),
    Topic(
        "crop yields",
        "AGRI",
        ("agriculture", "crop yield", "wheat", "soil", "fertiliser"),
        ("field trials", "measurements"),
        ("CSV", "XLSX", "GeoJSON"),
        0.1,
        {"nl": "gewasopbrengsten", "de": "Ernteerträge", "fr": "rendements des cultures"},
    ),
    Topic(
        "household energy consumption",
        "ENER",
        ("energy", "electricity", "smart meters", "heating", "households"),
        ("measurements", "panel"),
        ("CSV", "Parquet"),
        0.3,
        {
            "nl": "energieverbruik van huishoudens",
            "de": "Energieverbrauch der Haushalte",
            "fr": "consommation d'énergie des ménages",
        },
    ),
    Topic(
        "cycling and commuting",
        "TRAN",
        ("mobility", "cycling", "commuting", "traffic counts", "bicycles"),
        ("measurements", "survey"),
        ("CSV", "GeoJSON"),
        0.05,
        {
            "nl": "fietsen en woon-werkverkeer",
            "de": "Radfahren und Pendeln",
            "fr": "vélo et trajets domicile-travail",
        },
    ),
]
WD = "http://www.wikidata.org/entity/"
MESH = "http://id.nlm.nih.gov/mesh/"
# Real concepts (verified against Wikidata, MeSH and EDAM on 2026-11-19), set as
# dct:subject so that label resolution (ADR-0006) has something to resolve.
CONCEPTS = {
    "breast cancer": (WD + "Q128581", MESH + "D001943"),
    "colorectal cancer": (WD + "Q188874", MESH + "D015179"),
    "type 2 diabetes": (WD + "Q3025883", MESH + "D003924"),
    "heart failure": (WD + "Q181754", MESH + "D006333"),
    "dementia": (WD + "Q83030", MESH + "D003704"),
    "asthma in children": (WD + "Q35869", MESH + "D001249"),
    "rare metabolic disorders": (MESH + "D008661",),
    "whole-genome sequencing": (
        WD + "Q2068526",
        MESH + "D000073336",
        "http://edamontology.org/topic_0622",
    ),
    "COVID-19": (WD + "Q84263196", MESH + "D000086382"),
    "mental health of adolescents": (WD + "Q317309", MESH + "D008603"),
    "air quality": (WD + "Q56245086", MESH + "D000397"),
    "river water quality": (WD + "Q625376",),
    "urban heat islands": (WD + "Q215712",),
    "household income": (WD + "Q1591167",),
    "crop yields": (WD + "Q889514",),
    "household energy consumption": (WD + "Q16869822",),
    "cycling and commuting": (WD + "Q48442",),
}
# (in a title, in a sentence)
REGIONS = [
    ("national", "the whole country"),
    ("regional", "one region"),
    ("northern provinces", "the northern provinces"),
    ("coastal region", "the coastal region"),
    ("three hospitals", "three hospitals"),
    ("metropolitan area", "the metropolitan area"),
    ("rural municipalities", "rural municipalities"),
]
FORMAT_IRIS = {
    "CSV": f"{EU}file-type/CSV",
    "XLSX": f"{EU}file-type/XLSX",
    "JSON": f"{EU}file-type/JSON",
    "PDF": f"{EU}file-type/PDF",
    "NetCDF": f"{EU}file-type/NETCDF",
    "GeoJSON": f"{EU}file-type/GEOJSON",
    "GeoTIFF": f"{EU}file-type/TIFF",
    "Parquet": f"{EU}file-type/PARQUET",
}
MEDIA = {
    "CSV": "text/csv",
    "JSON": "application/json",
    "PDF": "application/pdf",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
LICENCES = [
    "http://creativecommons.org/licenses/by/4.0/",
    "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-4.0",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "http://creativecommons.org/licenses/by-nc/4.0/",
    "http://creativecommons.org/licenses/by-sa/4.0/",
]
ACCESS = {
    "open": f"{EU}access-right/PUBLIC",
    "restricted": f"{EU}access-right/RESTRICTED",
    "closed": f"{EU}access-right/NON_PUBLIC",
}
SERVICES = [
    ("Beacon API", "beacon"),
    ("OMOP CDM query service", "omop"),
    ("SPARQL endpoint", "sparql"),
    ("Data access request portal", "access"),
]


def entry_uuid(number: int) -> UUID:
    return uuid5(NAMESPACE, f"synthetic-fdp-{number}")


def client_url(number: int) -> str:
    return f"https://fdp-{number:03d}.{DOMAIN}"


class FdpBuilder:
    """One synthetic FDP: its nodes as rdflib graphs, keyed by node IRI."""

    def __init__(self, number: int, rng: random.Random) -> None:
        self.rng = rng
        self.base = client_url(number)
        self.country = rng.choice(list(COUNTRIES))
        self.language = COUNTRIES[self.country][1]
        place = rng.choice(PLACES)
        self.institution = f"{place} {rng.choice(INSTITUTION_KINDS)}"
        self.slug = f"org-{number:03d}"
        self.org = URIRef(f"https://{self.slug}.{DOMAIN}/")
        self.labs = [
            f"{place} {name}"
            for name in rng.sample(
                [
                    "Epidemiology Unit",
                    "Data Office",
                    "Clinical Research Group",
                    "Statistics Department",
                    "Imaging Lab",
                    "Field Station",
                ],
                3,
            )
        ]
        self.topics = rng.sample(TOPICS, rng.randint(2, 5))
        self.graphs: dict[str, Graph] = {}

    def graph(self, iri: str) -> Graph:
        return self.graphs.setdefault(iri, Graph())

    def literal_date(self, moment: datetime) -> Literal:
        return Literal(moment.date().isoformat(), datatype=XSD.date)

    def build(self, datasets: int) -> None:
        rng = self.rng
        root = URIRef(self.base)
        g = self.graph(self.base)
        g.add((root, RDF.type, FDPO.FAIRDataPoint))
        g.add((root, RDF.type, R3D.Repository))
        g.add((root, DCTERMS.title, Literal(f"{self.institution} FDP (synthetic)", lang="en")))
        g.add(
            (
                root,
                DCTERMS.description,
                Literal(
                    f"Synthetic metadata generated for testing the FDPneo Index. {self.institution} does not exist.",
                    lang="en",
                ),
            )
        )
        g.add((root, DCTERMS.publisher, self.org))
        g.add((self.org, FOAF.name, Literal(self.institution)))
        g.add((root, R3D.country, URIRef(f"http://lexvo.org/id/iso3166/{self.country}")))
        g.add((root, DCAT3.version, Literal(f"1.{rng.randint(0, 9)}.{rng.randint(0, 20)}")))
        catalogs = []
        for c, topic_group in enumerate(self._catalog_groups()):
            iri = f"{self.base}/catalog/{c + 1}"
            catalogs.append((iri, topic_group))
            g.add((root, FDPO.metadataCatalog, URIRef(iri)))
        per_catalog = max(1, datasets // max(1, len(catalogs)))
        for iri, topic_group in catalogs:
            self._catalog(iri, topic_group, per_catalog)

    def _catalog_groups(self) -> list[list[Topic]]:
        topics = list(self.topics)
        self.rng.shuffle(topics)
        groups = [topics[i : i + 2] for i in range(0, len(topics), 2)]
        return groups or [topics]

    def _catalog(self, iri: str, topics: list[Topic], datasets: int) -> None:
        rng = self.rng
        node = URIRef(iri)
        g = self.graph(iri)
        g.add((node, RDF.type, DCAT.Catalog))
        names = " and ".join(t.name for t in topics)
        g.add((node, DCTERMS.title, Literal(f"{names.capitalize()} data", lang="en")))
        if self.language != "en" and all(self.language in t.translations for t in topics):
            local = " / ".join(t.translations[self.language] for t in topics)
            g.add((node, DCTERMS.title, Literal(local, lang=self.language)))
        g.add(
            (
                node,
                DCTERMS.description,
                Literal(f"Datasets of the {self.institution} on {names}.", lang="en"),
            )
        )
        g.add((node, DCTERMS.publisher, self.org))
        g.add((self.org, FOAF.name, Literal(self.institution)))
        for theme in {t.theme for t in topics}:
            g.add((node, DCAT.theme, URIRef(f"{EU}data-theme/{theme}")))
        g.add((node, DCTERMS.issued, self.literal_date(self._date(2012, 2020))))
        dataset_iris = []
        for d in range(datasets):
            topic = rng.choice(topics)
            ds = f"{iri.replace('/catalog/', '/dataset/')}-{d + 1}"
            dataset_iris.append(ds)
            g.add((node, DCAT.dataset, URIRef(ds)))
            self._dataset(ds, topic)
        if rng.random() < 0.35 and dataset_iris:
            name, slug = rng.choice(SERVICES)
            service = f"{iri.replace('/catalog/', '/service/')}-{slug}"
            g.add((node, DCAT.service, URIRef(service)))
            s = self.graph(service)
            sn = URIRef(service)
            s.add((sn, RDF.type, DCAT.DataService))
            s.add((sn, DCTERMS.title, Literal(f"{name} — {self.institution}", lang="en")))
            s.add(
                (
                    sn,
                    DCTERMS.description,
                    Literal(f"{name} giving access to {names} data.", lang="en"),
                )
            )
            s.add((sn, DCAT.endpointURL, URIRef(f"{self.base}/api/{slug}")))
            for ds in rng.sample(dataset_iris, min(3, len(dataset_iris))):
                s.add((sn, DCAT.servesDataset, URIRef(ds)))

    def _date(self, first: int, last: int) -> datetime:
        start = datetime(first, 1, 1, tzinfo=UTC)
        end = datetime(last, 12, 31, tzinfo=UTC)
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def _dataset(self, iri: str, topic: Topic) -> None:
        rng = self.rng
        node = URIRef(iri)
        g = self.graph(iri)
        kind = rng.choice(topic.kinds)
        region, covering = rng.choice(REGIONS)
        start = rng.randint(2005, 2022)
        end = min(2026, start + rng.randint(1, 12))
        title = f"{topic.name.capitalize()} {kind}, {region}, {start}–{end}"
        g.add((node, RDF.type, DCAT.Dataset))
        g.add((node, DCTERMS.title, Literal(title, lang="en")))
        if self.language in topic.translations and rng.random() < 0.3:
            g.add(
                (
                    node,
                    DCTERMS.title,
                    Literal(
                        f"{topic.translations[self.language].capitalize()} ({start}–{end})",
                        lang=self.language,
                    ),
                )
            )
        people = int(rng.lognormvariate(8, 1.6))
        sentences = [
            f"A {kind} on {topic.name} covering {covering}, collected between {start} and {end}.",
            rng.choice(
                [
                    f"It includes {people:,} participants with repeated measurements.",
                    f"Records were linked to hospital and primary-care data for {people:,} people.",
                    f"Data were gathered by {rng.choice(self.labs)} using standardised protocols.",
                    f"Variables include {', '.join(rng.sample(topic.keywords, min(3, len(topic.keywords))))}.",
                ]
            ),
            rng.choice(
                [
                    "Access is granted after review by the data access committee.",
                    "The data are pseudonymised; a codebook is available.",
                    "Results have been published in peer-reviewed journals.",
                    "Quality checks follow the institute's data management plan.",
                ]
            ),
        ]
        g.add((node, DCTERMS.description, Literal(" ".join(sentences), lang="en")))
        for keyword in rng.sample(topic.keywords, rng.randint(2, min(5, len(topic.keywords)))):
            g.add((node, DCAT.keyword, Literal(keyword, lang="en")))
        # Providers differ: some put the EU theme in dct:subject, some put
        # MeSH or Wikidata concepts in dcat:theme (DCAT-AP 3 allows it).
        theme_predicate = DCTERMS.subject if rng.random() < 0.1 else DCAT.theme
        g.add((node, theme_predicate, URIRef(f"{EU}data-theme/{topic.theme}")))
        for concept in CONCEPTS.get(topic.name, ()):
            if rng.random() < 0.8:
                predicate = DCAT.theme if rng.random() < 0.25 else DCTERMS.subject
                g.add((node, predicate, URIRef(concept)))
        if rng.random() < 0.2:
            g.add(
                (
                    node,
                    DCAT.theme,
                    URIRef(f"{EU}data-theme/{rng.choice(['TECH', 'EDUC', 'GOVE', 'SOCI'])}"),
                )
            )
        roll = rng.random()
        access = "restricted" if roll < topic.restricted else "open"
        if access == "restricted" and rng.random() < 0.15:
            access = "closed"
        g.add((node, DCTERMS.accessRights, URIRef(ACCESS[access])))
        licence = rng.choice(LICENCES)
        g.add(
            (
                node,
                DCTERMS.license,
                URIRef(licence) if licence.startswith("http") else Literal(licence),
            )
        )
        g.add((node, DCTERMS.language, URIRef(f"{EU}language/ENG")))
        if self.language != "en" and rng.random() < 0.4:
            g.add((node, DCTERMS.language, URIRef(f"{EU}language/{LANGUAGES[self.language]}")))
        if rng.random() < 0.6:
            g.add((node, DCTERMS.spatial, URIRef(f"{EU}country/{COUNTRIES[self.country][0]}")))
        if rng.random() < 0.85:
            g.add((node, DCTERMS.publisher, self.org))
            g.add((self.org, FOAF.name, Literal(self.institution)))
        else:
            lab = BNode()
            g.add((node, DCTERMS.publisher, lab))
            g.add((lab, RDF.type, FOAF.Agent))
            g.add((lab, FOAF.name, Literal(rng.choice(self.labs))))
        issued = self._date(end - 1 if end <= 2026 else 2025, min(end + 1, 2026))
        g.add((node, DCTERMS.issued, self.literal_date(issued)))
        if rng.random() < 0.7:
            modified = min(
                issued + timedelta(days=rng.randint(10, 1500)), datetime(2026, 11, 15, tzinfo=UTC)
            )
            g.add(
                (
                    node,
                    DCTERMS.modified,
                    Literal(modified.isoformat(timespec="seconds"), datatype=XSD.dateTime),
                )
            )
        self._temporal(g, node, iri, start, end)
        g.add(
            (
                node,
                DCTERMS.identifier,
                Literal(f"https://doi.org/10.5072/{self.slug}.{iri.rsplit('/', 1)[-1]}"),
            )
        )
        contact = BNode()
        g.add((node, DCAT.contactPoint, contact))
        g.add((contact, VCARD.fn, Literal(rng.choice(self.labs))))
        g.add((contact, VCARD.hasEmail, URIRef(f"mailto:data-office@{self.slug}.{DOMAIN}")))
        if topic.theme == "HEAL" and rng.random() < 0.6:
            g.add((node, HEALTH.numberOfRecords, Literal(people, datatype=XSD.integer)))
        if rng.random() < 0.3:
            g.add(
                (
                    node,
                    DCTERMS.conformsTo,
                    URIRef(
                        rng.choice(
                            [
                                "https://healthdcat-ap.eu/R5",
                                "http://data.europa.eu/r5r/",
                                "https://health-ri.nl/core/v2",
                            ]
                        )
                    ),
                )
            )
        if rng.random() < 0.4:
            g.add(
                (
                    node,
                    DCAT.landingPage,
                    URIRef(f"https://{self.slug}.{DOMAIN}/datasets/{iri.rsplit('/', 1)[-1]}"),
                )
            )
        for x, fmt in enumerate(
            rng.sample(topic.formats, rng.randint(1, min(3, len(topic.formats))))
        ):
            self._distribution(
                f"{iri.replace('/dataset/', '/distribution/')}-{x + 1}", node, fmt, licence, access
            )

    @staticmethod
    def _temporal(g: Graph, node: URIRef, iri: str, start: int, end: int) -> None:
        """The years the title names, as ``dct:temporal`` in the forms FDPs use (TASKS 8.3).

        A generator of its own, so the rest of the corpus stays as it was.
        """
        rng = random.Random(f"{iri}#temporal")
        form = rng.random()
        if form < 0.05:
            return
        if form < 0.15:
            g.add((node, DCTERMS.temporal, Literal(f"{start}/{end}")))
            return
        period = BNode()
        g.add((node, DCTERMS.temporal, period))
        if form < 0.7:
            g.add((period, RDF.type, DCTERMS.PeriodOfTime))
            g.add((period, DCAT.startDate, Literal(f"{start}-01-01", datatype=XSD.date)))
            if rng.random() < 0.9:  # some are still running
                g.add((period, DCAT.endDate, Literal(f"{end}-12-31", datatype=XSD.date)))
        elif form < 0.85:
            g.add((period, SCHEMA.startDate, Literal(str(start), datatype=XSD.gYear)))
            g.add((period, SCHEMA.endDate, Literal(str(end), datatype=XSD.gYear)))
        else:
            beginning, ending = BNode(), BNode()
            g.add((period, TIME.hasBeginning, beginning))
            g.add((beginning, TIME.inXSDDate, Literal(f"{start}-01-01", datatype=XSD.date)))
            g.add((period, TIME.hasEnd, ending))
            g.add((ending, TIME.inXSDDate, Literal(f"{end}-12-31", datatype=XSD.date)))

    def _distribution(self, iri: str, dataset: URIRef, fmt: str, licence: str, access: str) -> None:
        rng = self.rng
        node = URIRef(iri)
        self.graph(str(dataset)).add((dataset, DCAT.distribution, node))
        g = self.graph(iri)
        g.add((node, RDF.type, DCAT.Distribution))
        label = {"PDF": "Codebook and documentation (PDF)"}.get(fmt, f"{fmt} export")
        if rng.random() < 0.9:
            g.add((node, DCTERMS.title, Literal(label, lang="en")))
        # Formats written the way FDPs write them: EU IRIs, plain strings, media types.
        if fmt in FORMAT_IRIS and rng.random() < 0.6:
            g.add((node, DCTERMS.format, URIRef(FORMAT_IRIS[fmt])))
        else:
            g.add((node, DCTERMS.format, Literal(rng.choice([fmt, fmt.lower()]))))
        if fmt in MEDIA and rng.random() < 0.5:
            g.add(
                (
                    node,
                    DCAT.mediaType,
                    URIRef(f"https://www.iana.org/assignments/media-types/{MEDIA[fmt]}"),
                )
            )
        if rng.random() < 0.5:
            g.add(
                (
                    node,
                    DCTERMS.license,
                    URIRef(licence) if licence.startswith("http") else Literal(licence),
                )
            )
        if access == "open" or fmt == "PDF":
            g.add(
                (
                    node,
                    DCAT.downloadURL,
                    URIRef(f"{self.base}/files/{iri.rsplit('/', 1)[-1]}.{fmt.lower()}"),
                )
            )
        else:
            g.add((node, DCAT.accessURL, URIRef(f"https://{self.slug}.{DOMAIN}/request-access")))


async def remove(database_url: str) -> int:
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    async with factory() as session, session.begin():
        ids = list(
            await session.scalars(
                select(IndexEntry.uuid).where(IndexEntry.client_url.like(f"%.{DOMAIN}%"))
            )
        )
        if ids:
            await session.execute(delete(IndexEntry).where(IndexEntry.uuid.in_(ids)))
    await engine.dispose()
    return len(ids)


async def seed(database_url: str, fdps: int, datasets: int, seed_value: int) -> tuple[int, int]:
    removed = await remove(database_url)
    if removed:
        print(f"replaced {removed} synthetic FDPs")
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    nodes_total = 0
    for number in range(1, fdps + 1):
        rng = random.Random(seed_value * 100_000 + number)
        builder = FdpBuilder(number, rng)
        builder.build(max(3, int(rng.lognormvariate(0, 0.8) * datasets)))
        url = client_url(number)
        repository = extract_repository_metadata(builder.graphs[url], url)
        assert repository is not None
        roll = rng.random()
        state = (
            EntryState.VALID
            if roll < 0.9
            else EntryState.UNREACHABLE
            if roll < 0.96
            else EntryState.INVALID
        )
        async with factory() as session, session.begin():
            entry = IndexEntry(
                uuid=entry_uuid(number),
                client_url=url,
                state=state,
                permit=EntryPermit.ACCEPTED,
                created_at=now - timedelta(days=rng.randint(30, 900)),
                updated_at=now,
                last_retrieval_at=now - timedelta(hours=rng.randint(1, 120)),
                last_success_at=now - timedelta(hours=rng.randint(1, 120)),
                repository_uri=repository.repository_uri,
                repository_metadata=dict(repository.metadata),
                # Their hosts do not exist: no liveness checks.
                next_check_at=now + timedelta(days=3650),
            )
            session.add(entry)
            await session.flush()  # the entry first: harvested nodes refer to it
            for iri, graph in builder.graphs.items():
                harvested = serialise(graph, iri)
                session.add(
                    HarvestedNode(
                        entry_uuid=entry.uuid,
                        node_iri=iri,
                        ntriples=harvested.ntriples,
                        content_hash=harvested.content_hash,
                        harvested_at=now,
                    )
                )
        nodes_total += len(builder.graphs)
        if number % 25 == 0 or number == fdps:
            print(f"{number}/{fdps} FDPs, {nodes_total} nodes", flush=True)
    await engine.dispose()
    return fdps, nodes_total


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--database-url", default=os.environ.get("FDPINDEX_DATABASE_URL", ""))
    parser.add_argument("--fdps", type=int, default=150)
    parser.add_argument("--datasets", type=int, default=60, help="typical datasets per FDP")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--remove", action="store_true", help="only remove the synthetic FDPs")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("no database URL (FDPINDEX_DATABASE_URL or --database-url)")
    if args.remove:
        print(f"removed {asyncio.run(remove(args.database_url))} synthetic FDPs")
        return
    fdps, nodes = asyncio.run(seed(args.database_url, args.fdps, args.datasets, args.seed))
    print(
        f"added {fdps} synthetic FDPs with {nodes} harvested nodes; restart the server to index them"
    )


if __name__ == "__main__":
    main()
