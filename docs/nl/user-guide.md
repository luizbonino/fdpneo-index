# De FDPneo Index gebruiken

*Vertaling van de [Engelse handleiding](../user-guide.md).*

Voor wie data zoekt: wat de index bevat en hoe daarin iets te vinden is.
Voor niets op deze pagina is een account nodig.

## Wat de index bevat

FAIR Data Points (FDP's) publiceren metadata over data: catalogi, datasets,
hun distributies (bestanden en API's) en dataservices. Ze melden zich bij
de index aan door hem te pingen. De index controleert daarna elke FDP
regelmatig en haalt de metadata op, zodat ze allemaal op één plek te
doorzoeken zijn.

- **Overzicht** (de startpagina): elke FDP die de index vermeldt, met zijn
  status. *Actief* betekent dat de index de FDP onlangs heeft bereikt;
  *Onbereikbaar* en *Ongeldige metadata* betekenen dat de laatste controles
  mislukt zijn; *Recent niet gezien* betekent dat de FDP al een tijd niet
  is gecontroleerd.
- **Zoeken**: de opgehaalde metadata van elke vermelde FDP.
- **SPARQL**: dezelfde metadata als kennisgraaf, voor precieze vragen.

## Zoeken

Typ woorden in het zoekvak. De zoekfunctie kijkt in titels, beschrijvingen,
trefwoorden, uitgevers en de namen van onderwerpen, in de talen van de
metadata zelf, en vindt ook andere vormen van een woord (zoeken op
*registry* vindt *registries*).

| Invoer | Resultaat |
|---|---|
| `breast cancer` | bronnen met beide woorden |
| `"lung function"` | precies die woordgroep |
| `cancer or tumour` | een van beide woorden |
| `cancer -lung` | *cancer* zonder *lung* |
| `title:registry` | *registry* in de titel (ook `description:`) |
| `keyword:asthma` | bronnen met dat trefwoord |
| `theme:Health`, `theme:HEAL` | een thema via zijn naam in een willekeurige taal, zijn code of zijn IRI |
| `publisher:"Alpha University"` | uitgevers waarvan de naam die woorden bevat |
| `identifier:10.5072/abc` | een DOI of een andere identificator |
| `-keyword:mouse` | zonder dat trefwoord |

Elk veld dat de index kent werkt op dezelfde manier (`format:csv`,
`licence:…`, `subject:…`). Een veldvoorvoegsel werkt als filter: de waarden
ervan staan aangevinkt in de filters aan de zijkant.

Levert een zoekopdracht niets op, dan stelt de index een correctie voor als
die wel iets vindt: bij *brest cancr* verschijnt **Bedoelde u breast
cancer**.

Als de index de namen van onderwerpen opzoekt (een keuze van de beheerder),
vindt een zoekopdracht onderwerpen ook via hun namen in andere talen en via
bredere termen: *borstkanker* of *Brustkrebs* vinden datasets die zijn
getagd met het Wikidata-item voor borstkanker, en *neoplasms* vindt datasets
met de tag *Breast Neoplasms*.

De tabbladen onder het zoekvak beperken de resultaten tot datasets,
catalogi, dataservices of FDP's.

### Filters

De filters aan de zijkant tellen per waarde hoeveel resultaten die zou
opleveren. Twee aangevinkte waarden van één filter tonen bronnen met de ene
of de andere waarde; vinkjes in verschillende filters moeten allemaal
gelden. Elk actief filter staat als label boven de resultaten; een klik
erop verwijdert het.

- **Thema**: de 13 brede datathema's van de EU, zoals Gezondheid.
- **Onderwerp**: specifieke onderwerpen uit vocabulaires zoals MeSH,
  Wikidata of EDAM. Hetzelfde onderwerp uit twee vocabulaires is één item
  wanneer een vocabulaire aangeeft dat ze overeenkomen (*dementia* van
  Wikidata is *Dementia* van MeSH). **Onderwerp als boom bekijken** toont
  elk onderwerp onder de bredere onderwerpen; een onderwerp dat daar wordt
  aangevinkt, vindt dat onderwerp en alles wat smaller is (*Neoplasms by
  Site* omvat *Breast Neoplasms*).
- **Trefwoord**, **Uitgever**, **Formaat**, **Licentie**,
  **Toegangsrechten**, **FAIR Data Point**, en meer onder **Meer filters**,
  waaronder **Geografische dekking**: de plaatsen waarop de data betrekking
  hebben. Een land is één item, hoe een FDP het ook benoemt (de lijst van de
  EU, GeoNames of een code zoals `NL`).
- **+ Filter toevoegen**: elke eigenschap die in de metadata voorkomt, de
  meest gebruikte eerst (bijvoorbeeld `dct:language` of `dct:conformsTo`).
  Toegevoegde filters blijven in het adres van de pagina staan, zodat een
  gedeelde link ze meeneemt, en deze browser onthoudt ze. **Verwijderen**
  haalt er een weg.

Numerieke filters, zoals **Aantal records**, tellen resultaten per bereik
en accepteren ook een eigen laagste en hoogste waarde. Ja/Nee-filters geven
aan of een bron iets überhaupt vermeldt (bijvoorbeeld of het om
persoonsgegevens gaat, bij indexen die de HealthDCAT-AP-filters gebruiken).

**Gebied** vindt data waarvan het gebied overlapt met een kader dat als vier
getallen wordt opgegeven: west, zuid, oost, noord (voor Nederland ongeveer
`3.3, 50.7, 7.3, 53.6`). Alleen bronnen die een gebied als coördinaten
vermelden, hebben er een.

**Datums** beperkt de resultaten op het moment waarop een bron is uitgegeven
of voor het laatst is gewijzigd. **Periode** vindt data waarvan de
temporele dekking overlapt met de opgegeven jaren: een jaar (`2010`), een
maand (`2010-05`) of een datum (`2010-05-31`), of met één kant open. Een
dataset over 2013 tot 2025 komt overeen met 2012–2013; een dataset zonder
einddatum komt overeen met elk later jaar. Bronnen die geen temporele
dekking vermelden, vallen weg zodra dit filter is ingesteld.

**Voldoet aan profiel** houdt de records over die deze index tegen een
metadataprofiel (DCAT-AP, HealthDCAT-AP …) heeft gecontroleerd en zonder
schendingen heeft bevonden; het resultaat van een dataset vermeldt aan
welke profielen die voldoet. Bij indexen voor gezondheidsdata houdt
**Kwaliteitscertificaat** de datasets over waarvan de houder een
kwaliteitscertificaat vermeldt; het certificaat zelf staat op de pagina van
de dataset. Sommige indexen tonen alleen records die aan een profiel
voldoen.

Bij zeer brede zoekopdrachten worden de filters op een steekproef geteld:
die aantallen staan er met ≈ bij.

### Resultaten

Elk resultaat toont het type, de herkomst (de FDP en de catalogus), een
beschrijving, de thema's, onderwerpen en trefwoorden (een klik erop filtert
daarop), de formaten van de bestanden en de periode waarop de data
betrekking hebben. Wanneer andere FDP's dezelfde bron vermelden, opent
**Ook in n andere FDP's** de lijst; de pagina van de bron toont ze onder
**Ook gepubliceerd in**. **Kopieën groeperen** toont zo'n bron één keer,
als de kopie die de zoekfunctie het hoogst rangschikt; het aantal geeft dan
aan voor hoeveel bronnen de groepen staan. **Openen bij de bron** gaat naar
de bron op zijn FDP. De eigen pagina van een bron toont elke waarde, de
bovenliggende en onderliggende bronnen, en de metadata zoals opgehaald
(Turtle, JSON-LD of N-Triples), en onder **Conformiteit** hoe de bron
scoort op elk profiel dat de index controleert: de schendingen, die de
regels van een profiel breken, en de waarschuwingen, die aanbevelingen
zijn.

**Link kopiëren** kopieert het adres van de zoekopdracht met de filters.
**Tonen als SPARQL** opent dezelfde zoekopdracht als SPARQL-query, om die
te verfijnen; de query geeft aan waar SPARQL de zoekopdracht alleen kan
benaderen (bijvoorbeeld bij het matchen van woorden en de rangschikking).

## SPARQL

De SPARQL-pagina biedt drie ingangen:

- **Vragen**: kant-en-klare query's met een formulier voor hun parameters,
  zoals "Datasets by theme", "Datasets by licence" of "FDPs by country".
  Geen SPARQL nodig.
- **Samensteller**: kies een type, voeg voorwaarden toe met suggesties uit
  de data, kies een lijst of een telling; de SPARQL wordt onderweg
  bijgewerkt.
- **Editor**: SPARQL schrijven met suggesties die passen bij de plek in de
  query: prefixen na `PREFIX`, de eigenschappen uit de data waar een
  eigenschap hoort, klassen na `a`, functies en variabelen binnen
  `FILTER( )` en `BIND( )`, en `ORDER BY`, `LIMIT` en dergelijke na het
  patroon. Fouten worden tijdens het typen gemarkeerd; `{ }`-blokken zijn in
  te klappen. Ctrl/Cmd+Enter voert de query uit.

Resultaten verschijnen als tabel, als grafiek wanneer ze een categorie en
een getal bevatten, of onbewerkt, en zijn te downloaden als CSV, TSV, JSON
of Turtle. Recente query's blijven bewaard in de browser; elke query heeft
een link die gedeeld kan worden.

### De data

De metadata van elke vermelde FDP is een named graph, met het adres van de
FDP als naam; de default graph is ze allemaal samen. De graph
`urn:fdpindex:entries` beschrijft de FDP's zelf: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Gangbare prefixen (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) hoeven niet gedeclareerd te worden.

```sparql
# Datasets per FDP
SELECT ?fdp (COUNT(?dataset) AS ?datasets)
WHERE { GRAPH ?fdp { ?dataset a dcat:Dataset } }
GROUP BY ?fdp
ORDER BY DESC(?datasets)
```

```sparql
# Datasets about a MeSH descriptor, with their publisher
SELECT ?dataset ?title ?publisher WHERE {
  ?dataset a dcat:Dataset ;
           dct:subject <http://id.nlm.nih.gov/mesh/D001943> ;
           dct:title ?title .
  OPTIONAL { ?dataset dct:publisher/foaf:name ?publisher }
}
```

```sparql
# Active FDPs and their countries
SELECT ?fdp ?title ?country WHERE {
  GRAPH <urn:fdpindex:entries> {
    ?fdp fdpindex:state "ACTIVE" ; dct:title ?title .
    OPTIONAL { ?fdp r3d:country ?country }
  }
}
```

### Vanuit een programma

Het endpoint volgt het SPARQL 1.1 Protocol op `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Resultaten komen als SPARQL JSON, XML, CSV of TSV; CONSTRUCT en DESCRIBE
als Turtle, N-Triples of JSON-LD. Alleen query's worden geaccepteerd (geen
updates, geen `SERVICE`). Elke query heeft een tijdsbudget en een maximum
aantal rijen, en voor anonieme aanroepers geldt een limiet per minuut;
`GET /index/config` geeft de waarden van deze index.

De zoekfunctie heeft ook een API: `GET /index/search` accepteert dezelfde
parameters als het adres van de zoekpagina; [API-voorbeelden](../api.md)
(in het Engels) laat dat zien, samen met de DCAT-AP-feed (`/index/catalog`)
en OAI-PMH (`/index/oai`) om alles op te halen. Zie `/docs` op de index
voor alle paden.

## Statistieken

**Statistieken** laat zien wat de index bevat en hoe dat verandert: vermelde
en actieve FDP's, records per type, de FDP's met de meeste records, het deel
van de records dat aan elk profiel voldoet, FDP's per maand van aanmelding en
datasets per jaar van uitgifte. Er wordt geteld wat bezoekers zien, net als in
de lijst van FDP's en het zoeken. Het verloop begint bij de eerste telling (er
wordt elk uur geteld; de laatste telling van de dag blijft bewaard), dus een
nieuwe index toont pas vanaf de tweede dag een lijn. Onder elke grafiek staat
**Als tabel tonen**, en **Openen als SPARQL-query** stelt dezelfde vraag in
SPARQL, waar u die kunt aanpassen; SPARQL telt per RDF-type, dus de getallen
kunnen iets afwijken.

Twee kaarten tonen waar de FDP's zijn (geplaatst door de keuze van een
beheerder, de FDP zelf, de uitgever, het domein of de server, in die
volgorde) en waarover de data gaat (de ruimtelijke dekking van de records);
kies een land op de tweede kaart om de records ervan te zoeken.

## Talen

De interface is beschikbaar in het Engels, Nederlands, Duits, Frans, Spaans,
Italiaans en Portugees: het menu bovenaan kiest een taal, en de index
onthoudt die in deze browser (in het begin volgt hij de talen van de
browser). Titels, beschrijvingen en labels uit de metadata volgen de
interfacetaal wanneer een FDP ze in die taal levert, en anders de
terugvaltalen van de index; met **Metadata in** op de zoekpagina is er een
andere taal voor te kiezen.

## Een FDP registreren

Zie de pagina **FDP registreren**: configureer de FDP zo dat die de index
pingt, of vul daar het adres in om de FDP te registreren en meteen te zien
wat de index heeft gevonden. [FDP's en de index](../data-holders.md) (in
het Engels) legt uit wat er daarna gebeurt en hoe het conformiteitsrapport
van een FDP te lezen is.
