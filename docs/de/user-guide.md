# Den FDPneo Index nutzen

*Übersetzung der [englischen Anleitung](../user-guide.md).*

Für alle, die Daten suchen: was der Index enthält und wie Sie darin etwas
finden. Für nichts auf dieser Seite ist ein Konto nötig.

## Was der Index enthält

FAIR Data Points (FDPs) veröffentlichen Metadaten über Daten: Kataloge,
Datensätze, deren Distributionen (Dateien und APIs) und Datendienste. Sie
melden sich beim Index an, indem sie ihm einen Ping senden. Der Index prüft
danach jeden FDP regelmäßig und ruft seine Metadaten ab, sodass Sie alle an
einem Ort durchsuchen können.

- **Verzeichnis** (die Startseite): jeder FDP, den der Index führt, mit
  seinem Status. *Aktiv* bedeutet, dass der Index ihn kürzlich erreicht hat;
  *Nicht erreichbar* und *Ungültige Metadaten* bedeuten, dass die letzten
  Prüfungen fehlgeschlagen sind; *Länger nicht gesehen* bedeutet, dass er
  eine Weile nicht geprüft wurde.
- **Suche**: die abgerufenen Metadaten aller gelisteten FDPs.
- **SPARQL**: dieselben Metadaten als Wissensgraph, für präzise Fragen.

## Suchen

Geben Sie Wörter in das Suchfeld ein. Die Suche durchsucht Titel,
Beschreibungen, Schlagwörter, Herausgeber und die Namen von Sachgebieten,
in den Sprachen der Metadaten selbst, und findet auch andere Formen eines
Wortes (eine Suche nach *registry* findet *registries*).

| Sie geben ein | Gefunden wird |
|---|---|
| `breast cancer` | Ressourcen mit beiden Wörtern |
| `"lung function"` | genau diese Wortfolge |
| `cancer or tumour` | eines der beiden Wörter |
| `cancer -lung` | *cancer* ohne *lung* |
| `title:registry` | *registry* im Titel (auch `description:`) |
| `keyword:asthma` | Ressourcen mit diesem Schlagwort |
| `theme:Health`, `theme:HEAL` | ein Thema über seinen Namen in einer beliebigen Sprache, seinen Code oder seine IRI |
| `publisher:"Alpha University"` | Herausgeber, deren Name diese Wörter enthält |
| `identifier:10.5072/abc` | eine DOI oder eine andere Kennung |
| `-keyword:mouse` | ohne dieses Schlagwort |

Jedes Feld des Index funktioniert genauso (`format:csv`, `licence:…`,
`subject:…`). Ein Feldpräfix wirkt als Filter: Seine Werte erscheinen in
den Filtern an der Seite als angehakt.

Findet eine Suche nichts, schlägt sie eine Korrektur vor, sofern diese
etwas findet: Zu *brest cancr* erscheint **Meinten Sie breast cancer**.

Wenn der Index die Namen von Sachgebieten auflöst (eine Entscheidung der
Administration), findet eine Suche Sachgebiete auch über ihre Namen in
anderen Sprachen und über übergeordnete Begriffe: *borstkanker* oder
*Brustkrebs* finden Datensätze, die mit dem Wikidata-Eintrag für Brustkrebs
versehen sind, und *neoplasms* findet solche mit *Breast Neoplasms*.

Die Reiter unter dem Suchfeld beschränken die Ergebnisse auf Datensätze,
Kataloge, Datendienste oder FDPs.

### Filter

Die Filter an der Seite zählen für jeden Wert, wie viele Ergebnisse er
liefern würde. Wenn Sie zwei Werte eines Filters anhaken, werden
Ressourcen mit dem einen oder dem anderen angezeigt; Häkchen in
verschiedenen Filtern müssen alle zutreffen. Jeder aktive Filter erscheint
als Chip über den Ergebnissen; ein Klick darauf entfernt ihn.

- **Thema**: die 13 übergreifenden Datenthemen der EU, etwa Gesundheit.
- **Sachgebiet**: spezifische Themen aus Vokabularen wie MeSH, Wikidata
  oder EDAM. Dasselbe Thema aus zwei Vokabularen ist ein einziger Eintrag,
  wenn ein Vokabular angibt, dass sie übereinstimmen (*dementia* aus
  Wikidata ist *Dementia* aus MeSH). **Sachgebiet als Baum durchsuchen**
  zeigt jedes Sachgebiet unter seinen übergeordneten; wenn Sie dort eines
  anhaken, finden Sie es und alles Untergeordnete (*Neoplasms by Site*
  umfasst *Breast Neoplasms*).
- **Schlagwort**, **Herausgeber**, **Format**, **Lizenz**,
  **Zugriffsrechte**, **FAIR Data Point** und weitere unter **Weitere
  Filter**, darunter **Räumliche Abdeckung**: die Orte, die die Daten
  abdecken. Ein Land ist ein einziger Eintrag, wie auch immer ein FDP es
  benennt (die Liste der EU, GeoNames oder ein Code wie `NL`).
- **+ Filter hinzufügen**: jede Eigenschaft, die in den Metadaten vorkommt,
  die meistgenutzten zuerst (zum Beispiel `dct:language` oder
  `dct:conformsTo`). Hinzugefügte Filter bleiben in der Adresse der Seite,
  sodass ein geteilter Link sie mitnimmt, und dieser Browser merkt sie
  sich. **Entfernen** nimmt einen wieder weg.

Zahlenfilter wie **Anzahl der Einträge** zählen Ergebnisse in Bereichen
und nehmen auch einen eigenen kleinsten und größten Wert an. Ja/Nein-Filter
geben an, ob eine Ressource überhaupt eine Angabe macht (zum Beispiel, ob
sie personenbezogene Daten betrifft, bei Indizes, die die
HealthDCAT-AP-Filter verwenden).

**Gebiet** findet Daten, deren Gebiet sich mit einem Rechteck überschneidet,
das Sie als vier Zahlen angeben: West, Süd, Ost, Nord (für die Niederlande
etwa `3.3, 50.7, 7.3, 53.6`). Nur Ressourcen, die ein Gebiet als
Koordinaten angeben, haben eines.

**Datumsangaben** schränkt danach ein, wann eine Ressource veröffentlicht
oder zuletzt geändert wurde. **Zeitraum** findet Daten, deren zeitliche
Abdeckung sich mit den angegebenen Jahren überschneidet: Geben Sie ein Jahr
(`2010`), einen Monat (`2010-05`) oder ein Datum (`2010-05-31`) ein, oder
lassen Sie eine Seite offen. Ein Datensatz für 2013 bis 2025 passt zu
2012–2013; einer ohne Enddatum passt zu jedem späteren Jahr. Ressourcen, die
keine zeitliche Abdeckung angeben, fallen weg, sobald dieser Filter gesetzt
ist.

**Erfüllt Profil** behält Einträge, die dieser Index gegen ein
Metadatenprofil (DCAT-AP, HealthDCAT-AP …) geprüft und ohne Verstöße
befunden hat; das Ergebnis eines Datensatzes nennt die Profile, die er
erfüllt. Bei Indizes für Gesundheitsdaten behält **Qualitätszertifikat**
Datensätze, deren Halter ein Qualitätszertifikat angibt; das Zertifikat
selbst steht auf der Seite des Datensatzes. Manche Indizes zeigen nur
Einträge, die ein Profil erfüllen.

Sehr breite Suchen zählen ihre Filter anhand einer Stichprobe: Diese Zahlen
erscheinen mit ≈.

### Ergebnisse

Jedes Ergebnis zeigt seinen Typ, seine Herkunft (den FDP und den Katalog),
eine Beschreibung, seine Themen, Sachgebiete und Schlagwörter (ein Klick
darauf filtert danach), die Formate seiner Dateien und den Zeitraum, den
seine Daten abdecken. Wenn andere FDPs dieselbe Ressource führen, öffnet
**Auch in n weiteren FDPs** die Liste; die Seite der Ressource zeigt sie
unter **Auch veröffentlicht in**. **Kopien gruppieren** zeigt eine solche
Ressource nur einmal, als die Kopie, die die Suche am höchsten einstuft;
die Anzahl gibt dann an, für wie viele Ressourcen die Gruppen stehen. **An
der Quelle öffnen** führt zur Ressource auf ihrem FDP. Die eigene Seite
einer Ressource zeigt jeden Wert, ihre über- und untergeordneten
Ressourcen und ihre Metadaten wie abgerufen (Turtle, JSON-LD oder
N-Triples) sowie unter **Konformität**, wie sie bei jedem Profil
abschneidet, das der Index prüft: ihre Verstöße, die gegen die Regeln
eines Profils verstoßen, und ihre Warnungen, die Empfehlungen sind.

**Link kopieren** kopiert die Adresse der Suche mit ihren Filtern. **Als
SPARQL anzeigen** öffnet dieselbe Suche als SPARQL-Abfrage, um sie zu
verfeinern; die Abfrage gibt an, wo SPARQL die Suche nur annähern kann
(etwa beim Abgleich von Wörtern und bei der Rangfolge).

## SPARQL

Die SPARQL-Seite bietet drei Zugänge:

- **Fragen**: fertige Abfragen mit einem Formular für ihre Parameter, etwa
  „Datasets by theme“, „Datasets by licence“ oder „FDPs by country“. Kein
  SPARQL nötig.
- **Baukasten**: Wählen Sie einen Typ, fügen Sie Bedingungen mit
  Vorschlägen aus den Daten hinzu, wählen Sie eine Liste oder eine Zählung;
  das SPARQL wird dabei laufend aktualisiert.
- **Editor**: SPARQL schreiben, mit Vorschlägen passend zur jeweiligen
  Stelle: Präfixe nach `PREFIX`, die Eigenschaften aus den Daten dort, wo
  eine Eigenschaft hingehört, Klassen nach `a`, Funktionen und Variablen
  innerhalb von `FILTER( )` und `BIND( )` sowie `ORDER BY`, `LIMIT` und
  Ähnliches nach dem Muster. Fehler werden beim Tippen markiert;
  `{ }`-Blöcke lassen sich einklappen. Strg/Cmd+Enter führt die Abfrage
  aus.

Ergebnisse erscheinen als Tabelle, als Diagramm, wenn sie eine Kategorie
und eine Zahl enthalten, oder als Rohdaten, und lassen sich als CSV, TSV,
JSON oder Turtle herunterladen. Letzte Abfragen bleiben in Ihrem Browser
gespeichert; jede Abfrage hat einen Link, den Sie teilen können.

### Die Daten

Die Metadaten jedes gelisteten FDP bilden einen benannten Graphen, benannt
nach der Adresse des FDP; der Standardgraph umfasst sie alle zusammen. Der
Graph `urn:fdpindex:entries` beschreibt die FDPs selbst: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Gängige Präfixe (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) müssen nicht deklariert werden.

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

### Aus einem Programm

Der Endpunkt folgt dem SPARQL 1.1 Protocol unter `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Ergebnisse kommen als SPARQL JSON, XML, CSV oder TSV; CONSTRUCT und
DESCRIBE als Turtle, N-Triples oder JSON-LD. Es werden nur Abfragen
angenommen (keine Updates, kein `SERVICE`). Jede Abfrage hat ein Zeitbudget
und eine Obergrenze für Zeilen, anonyme Aufrufer zudem ein Limit pro
Minute; `GET /index/config` nennt Ihnen die Werte dieses Index.

Die Suche hat auch eine API: `GET /index/search` nimmt dieselben Parameter
wie die Adresse der Suchseite; [API-Beispiele](../api.md) (auf Englisch)
zeigt sie, zusammen mit dem DCAT-AP-Feed (`/index/catalog`) und OAI-PMH
(`/index/oai`), um alles abzurufen. Unter `/docs` auf dem Index finden Sie
alle Pfade.

## Sprachen

Die Oberfläche gibt es auf Englisch, Niederländisch, Deutsch, Französisch,
Spanisch, Italienisch und Portugiesisch: Das Menü oben wählt eine Sprache,
und der Index merkt sie sich in diesem Browser (zu Beginn folgt er den
Sprachen Ihres Browsers). Titel, Beschreibungen und Bezeichnungen aus den
Metadaten folgen der Sprache der Oberfläche, wenn ein FDP sie darin
bereitstellt, sonst den Ausweichsprachen des Index; mit **Metadaten auf**
auf der Suchseite wählen Sie eine andere Sprache dafür.

## Ihren FDP registrieren

Siehe die Seite **FDP registrieren**: Konfigurieren Sie Ihren FDP so, dass
er dem Index einen Ping sendet, oder geben Sie dort seine Adresse ein, um
ihn zu registrieren und sofort zu sehen, was der Index gefunden hat.
[FDPs und der Index](../data-holders.md) (auf Englisch) erklärt, was danach
geschieht und wie Sie den Konformitätsbericht Ihres FDP lesen.
