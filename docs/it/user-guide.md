# Uso dell'FDPneo Index

*Traduzione della [guida in inglese](../user-guide.md).*

Per chi cerca dati: cosa contiene l'indice e come trovarvi ciò che serve.
Nulla di quanto descritto in questa pagina richiede un account.

## Cosa contiene l'indice

I FAIR Data Point (FDP) pubblicano metadati sui dati: cataloghi, dataset, le
loro distribuzioni (file e API) e servizi di dati. Si registrano presso
l'indice inviandogli un ping. L'indice verifica poi regolarmente ogni FDP e
ne raccoglie i metadati, così che si possa cercare in tutti da un unico
punto.

- **Elenco** (la pagina iniziale): tutti gli FDP elencati dall'indice, con il
  loro stato. *Attivo* significa che l'indice lo ha raggiunto di recente;
  *Non raggiungibile* e *Metadati non validi* significano che le ultime
  verifiche non sono riuscite; *Non visto di recente* significa che non viene
  verificato da un po' di tempo.
- **Ricerca**: i metadati raccolti di tutti gli FDP elencati.
- **SPARQL**: gli stessi metadati come grafo di conoscenza, per domande
  precise.

## Cercare

Si scrivono parole nella casella di ricerca. La ricerca guarda in titoli,
descrizioni, parole chiave, editori e nomi degli argomenti, nelle lingue dei
metadati stessi, e trova altre forme di una parola (cercando *registry* si
trova *registries*).

| Cosa si scrive | Cosa si trova |
|---|---|
| `breast cancer` | risorse con entrambe le parole |
| `"lung function"` | esattamente quella frase |
| `cancer or tumour` | l'una o l'altra parola |
| `cancer -lung` | *cancer* senza *lung* |
| `title:registry` | *registry* nel titolo (anche `description:`) |
| `keyword:asthma` | risorse con quella parola chiave |
| `theme:Health`, `theme:HEAL` | un tema per nome in qualsiasi lingua, per codice o per IRI |
| `publisher:"Alpha University"` | editori il cui nome contiene quelle parole |
| `identifier:10.5072/abc` | un DOI o un altro identificativo |
| `-keyword:mouse` | senza quella parola chiave |

Qualsiasi campo presente nell'indice funziona allo stesso modo
(`format:csv`, `licence:…`, `subject:…`). Un prefisso di campo agisce da
filtro: i suoi valori appaiono spuntati nei filtri a lato.

Una ricerca che non trova nulla propone una correzione quando questa trova
qualcosa: per *brest cancr* viene proposto **Forse si intendeva breast
cancer**.

Quando l'indice risolve i nomi degli argomenti (una scelta
dell'amministratore), una ricerca trova gli argomenti anche per i loro nomi
in altre lingue e per termini più generali: *borstkanker* o *Brustkrebs*
trovano i dataset etichettati con l'elemento Wikidata per il cancro al seno,
e *neoplasms* trova quelli etichettati *Breast Neoplasms*.

Le schede sotto la casella restringono i risultati a dataset, cataloghi,
servizi di dati o FDP.

### Filtri

I filtri a lato contano, per ogni valore, quanti risultati darebbe.
Spuntando due valori di uno stesso filtro si vedono le risorse che hanno
l'uno o l'altro; le spunte in filtri diversi devono valere tutte. Ogni filtro
attivo appare come un'etichetta sopra i risultati; un clic sull'etichetta lo
rimuove.

- **Tema**: i 13 grandi temi di dati dell'UE, come Salute.
- **Argomento**: temi specifici da vocabolari come MeSH, Wikidata o EDAM. Lo
  stesso tema proveniente da due vocabolari è una sola voce quando un
  vocabolario indica che corrispondono (*dementia* di Wikidata è *Dementia*
  di MeSH).
  **Sfogliare Argomento come albero** mostra ogni argomento sotto quelli più
  generali; spuntandone uno lì si trovano quell'argomento e tutto ciò che è
  più specifico (*Neoplasms by Site* comprende *Breast Neoplasms*).
- **Parola chiave**, **Editore**, **Formato**, **Licenza**, **Diritti di
  accesso**, **FAIR Data Point**, e altri in **Altri filtri**, tra cui
  **Copertura geografica**: i luoghi a cui si riferiscono i dati. Un paese è
  una sola voce comunque un FDP lo indichi (l'elenco dell'UE, GeoNames o un
  codice come `NL`).
- **+ Aggiungere un filtro**: qualsiasi proprietà presente nei metadati,
  prima le più usate (ad esempio `dct:language` o `dct:conformsTo`). I filtri
  aggiunti restano nell'indirizzo della pagina, quindi un link condiviso li
  include, e questo browser li ricorda. **Rimuovere** ne toglie uno.

I filtri numerici, come **Numero di record**, contano i risultati per
intervalli e accettano anche un valore minimo e un valore massimo a scelta.
I filtri Sì/No indicano se una risorsa dichiara o meno qualcosa (ad esempio
se riguarda dati personali, negli indici che usano i filtri di
HealthDCAT-AP).

**Area** trova i dati la cui area si sovrappone a un riquadro indicato con
quattro numeri: ovest, sud, est, nord (per i Paesi Bassi, circa
`3.3, 50.7, 7.3, 53.6`). Hanno un'area solo le risorse che la dichiarano
come coordinate.

**Date** restringe in base a quando una risorsa è stata pubblicata o
modificata l'ultima volta. **Periodo coperto** trova i dati la cui copertura
temporale si sovrappone agli anni indicati: si scrive un anno (`2010`), un
mese (`2010-05`) o una data (`2010-05-31`), oppure si lascia aperto uno dei
due estremi. Un dataset che copre dal 2013 al 2025 corrisponde a 2012–2013;
uno senza data di fine corrisponde a tutti gli anni successivi. Le risorse
che non dichiarano una copertura temporale vengono escluse non appena si
imposta questo filtro.

**Conforme al profilo** mantiene i record che questo indice ha verificato
rispetto a un profilo di metadati (DCAT-AP, HealthDCAT-AP …) senza trovare
violazioni; il risultato di un dataset indica quali profili rispetta. Negli
indici per dati sanitari, **Certificato di qualità** mantiene i dataset il
cui titolare dichiara un certificato di qualità; il certificato stesso si
trova nella pagina del dataset. Alcuni indici mostrano solo i record conformi
a un profilo.

Le ricerche molto ampie contano i filtri su un campione: quei conteggi sono
indicati con ≈.

### Risultati

Ogni risultato mostra il tipo, la provenienza (l'FDP e il catalogo), una
descrizione, i temi, gli argomenti e le parole chiave (un clic su uno di essi
filtra in base a quel valore), i formati dei file e il periodo coperto dai
dati. Quando altri FDP elencano la stessa risorsa, **Anche in n altri FDP**
apre l'elenco; la pagina della risorsa li mostra sotto **Pubblicata anche
in**. **Raggruppare le copie** mostra una tale risorsa una sola volta, come
la copia che la ricerca colloca per prima; il conteggio indica allora quante
risorse rappresentano i gruppi. **Aprire alla fonte** porta alla risorsa nel
suo FDP. La pagina di una risorsa mostra tutti i valori, i genitori e i
figli, i metadati così come sono stati raccolti (Turtle, JSON-LD o
N-Triples) e, sotto **Conformità**, come si comporta rispetto a ciascun
profilo verificato dall'indice: le violazioni, che infrangono le regole di
un profilo, e gli avvisi, che sono raccomandazioni.

**Copiare il link** copia l'indirizzo della ricerca con i suoi filtri.
**Mostrare come SPARQL** apre la stessa ricerca come query SPARQL, per
affinarla; la query indica dove SPARQL può solo approssimare la ricerca (ad
esempio nella corrispondenza delle parole e nell'ordinamento).

## SPARQL

La pagina SPARQL offre tre modi di accesso:

- **Domande**: query pronte con un modulo per i parametri, come "Datasets by
  theme", "Datasets by licence" o "FDPs by country". Non serve conoscere
  SPARQL.
- **Costruttore**: si sceglie un tipo, si aggiungono condizioni con
  suggerimenti tratti dai dati, si sceglie un elenco o un conteggio; lo
  SPARQL si aggiorna man mano.
- **Editor**: si scrive SPARQL con suggerimenti adatti al punto in cui ci si
  trova: prefissi dopo `PREFIX`, le proprietà presenti nei dati dove va una
  proprietà, classi dopo `a`, funzioni e variabili dentro `FILTER( )` e
  `BIND( )`, e `ORDER BY`, `LIMIT` e simili dopo il pattern. Gli errori
  vengono segnalati durante la digitazione; i blocchi `{ }` si possono
  comprimere. Ctrl/Cmd+Enter esegue la query.

I risultati si visualizzano come tabella, come grafico quando hanno una
categoria e un numero, o in forma grezza, e si scaricano come CSV, TSV, JSON
o Turtle. Le query recenti restano nel browser; ogni query ha un link
condivisibile.

### I dati

I metadati di ogni FDP elencato formano un grafo con nome, denominato con
l'indirizzo dell'FDP; il grafo predefinito è l'insieme di tutti. Il grafo
`urn:fdpindex:entries` descrive gli FDP stessi: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. I prefissi comuni (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) non richiedono dichiarazione.

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

### Da un programma

L'endpoint segue il protocollo SPARQL 1.1 in `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

I risultati arrivano come SPARQL JSON, XML, CSV o TSV; quelli di CONSTRUCT e
DESCRIBE come Turtle, N-Triples o JSON-LD. Sono accettate solo query (niente
aggiornamenti, niente `SERVICE`). Ogni query ha un tempo massimo e un limite
di righe, e i chiamanti anonimi un limite di richieste al minuto;
`GET /index/config` indica i valori di questo indice.

Anche la ricerca ha un'API: `GET /index/search` accetta gli stessi parametri
dell'indirizzo della pagina di ricerca; [Esempi di API](../api.md) (in
inglese) la illustra, insieme al feed DCAT-AP (`/index/catalog`) e a OAI-PMH
(`/index/oai`) per raccogliere tutto. Tutti i percorsi sono descritti in
`/docs` sull'indice.

## Statistiche

**Statistiche** mostra che cosa contiene l'indice e come cambia: FDP elencati
e attivi, schede per tipo, gli FDP con più schede, la quota di schede conformi
a ciascun profilo, gli FDP per mese di registrazione e i dataset per anno di
pubblicazione. Conta ciò che vedono i visitatori, come l'elenco degli FDP e la
ricerca. Gli andamenti iniziano con il primo conteggio (ogni ora; resta
l'ultimo del giorno), quindi un indice nuovo mostra una linea solo dal secondo
giorno. Ogni grafico ha **Mostra come tabella**, e **Apri come query SPARQL**
pone la stessa domanda in SPARQL, dove è possibile modificarla; SPARQL conta
per tipo RDF, quindi i numeri possono differire leggermente.

## Lingue

L'interfaccia è disponibile in inglese, olandese, tedesco, francese,
spagnolo, italiano e portoghese: il menu in alto permette di sceglierne una,
e l'indice la ricorda in questo browser (all'inizio segue le lingue del
browser). Titoli, descrizioni ed etichette dei metadati seguono la lingua
dell'interfaccia quando un FDP li fornisce in quella lingua, altrimenti le
lingue di riserva dell'indice; **Metadati in**, nella pagina di ricerca,
permette di scegliere un'altra lingua per essi.

## Registrare il proprio FDP

Si veda la pagina **Registrare un FDP**: configurare l'FDP perché invii un
ping all'indice, oppure inserirne lì l'indirizzo per registrarlo e vedere
subito cosa ha trovato l'indice. [Gli FDP e l'indice](../data-holders.md) (in
inglese) spiega cosa succede dopo e come leggere il rapporto di conformità
dell'FDP.
