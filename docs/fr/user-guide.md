# Utiliser le FDPneo Index

*Traduction du [guide en anglais](../user-guide.md).*

Pour les personnes qui cherchent des données : ce que contient l’index, et
comment y trouver ce que vous cherchez. Aucun compte n’est nécessaire pour
quoi que ce soit sur cette page.

## Ce que contient l’index

Les FAIR Data Points (FDP) publient des métadonnées sur des données :
catalogues, jeux de données, leurs distributions (fichiers et API) et
services de données. Ils s’enregistrent auprès de l’index en lui envoyant
un ping. L’index vérifie ensuite chaque FDP régulièrement et moissonne ses
métadonnées, pour que vous puissiez les interroger tous au même endroit.

- **Annuaire** (la page d’accueil) : chaque FDP répertorié par l’index,
  avec son état. *Actif* signifie que l’index l’a joint récemment ;
  *Injoignable* et *Métadonnées non valides* signifient que les dernières
  vérifications ont échoué ; *Pas vu récemment* signifie qu’il n’a pas été
  vérifié depuis un certain temps.
- **Recherche** : les métadonnées moissonnées de chaque FDP répertorié.
- **SPARQL** : les mêmes métadonnées sous forme de graphe de connaissances,
  pour des questions précises.

## Rechercher

Saisissez des mots dans la zone de recherche. La recherche porte sur les
titres, les descriptions, les mots-clés, les éditeurs et les noms de
sujets, dans les langues propres aux métadonnées, et trouve aussi d’autres
formes d’un mot (chercher *registry* trouve *registries*).

| Vous tapez | Résultat |
|---|---|
| `breast cancer` | les ressources contenant les deux mots |
| `"lung function"` | cette expression exacte |
| `cancer or tumour` | l’un ou l’autre mot |
| `cancer -lung` | *cancer* sans *lung* |
| `title:registry` | *registry* dans le titre (aussi `description:`) |
| `keyword:asthma` | les ressources ayant ce mot-clé |
| `theme:Health`, `theme:HEAL` | un thème par son nom dans n’importe quelle langue, son code ou son IRI |
| `publisher:"Alpha University"` | les éditeurs dont le nom contient ces mots |
| `identifier:10.5072/abc` | un DOI ou un autre identifiant |
| `-keyword:mouse` | sans ce mot-clé |

Tout champ de l’index fonctionne de la même façon (`format:csv`,
`licence:…`, `subject:…`). Un préfixe de champ agit comme un filtre : ses
valeurs apparaissent cochées dans les filtres sur le côté.

Une recherche sans résultat propose une correction lorsque celle-ci trouve
quelque chose : pour *brest cancr*, elle affiche **Vouliez-vous dire breast
cancer**.

Lorsque l’index résout les noms de sujets (un choix de l’administrateur),
une recherche trouve aussi les sujets par leurs noms dans d’autres langues
et par des termes plus larges : *borstkanker* ou *Brustkrebs* trouvent les
jeux de données étiquetés avec l’élément Wikidata du cancer du sein, et
*neoplasms* trouve ceux étiquetés *Breast Neoplasms*.

Les onglets sous la zone de recherche limitent les résultats aux jeux de
données, aux catalogues, aux services de données ou aux FDP.

### Filtres

Les filtres sur le côté indiquent, pour chaque valeur, combien de résultats
elle donnerait. Cocher deux valeurs d’un même filtre affiche les ressources
ayant l’une ou l’autre ; les cases cochées dans des filtres différents
doivent toutes être satisfaites. Chaque filtre actif apparaît sous forme
de pastille au-dessus des résultats ; cliquez dessus pour le retirer.

- **Thème** : les 13 grands thèmes de données de l’UE, comme Santé.
- **Sujet** : des sujets précis issus de vocabulaires comme MeSH, Wikidata
  ou EDAM. Un même sujet provenant de deux vocabulaires forme une seule
  entrée lorsqu’un vocabulaire indique qu’ils correspondent (*dementia* de
  Wikidata est *Dementia* de MeSH). **Parcourir Sujet sous forme
  d’arborescence** affiche chaque sujet sous ses sujets plus larges ; en
  cocher un à cet endroit le trouve, ainsi que tout ce qui est plus
  spécifique (*Neoplasms by Site* inclut *Breast Neoplasms*).
- **Mot-clé**, **Éditeur**, **Format**, **Licence**, **Droits d'accès**,
  **FAIR Data Point**, et d’autres sous **Plus de filtres**, dont
  **Couverture géographique** : les lieux couverts par les données. Un pays
  forme une seule entrée, quelle que soit la façon dont un FDP le nomme (la
  liste de l’UE, GeoNames ou un code comme `NL`).
- **+ Ajouter un filtre** : toute propriété présente dans les métadonnées,
  les plus utilisées d’abord (par exemple `dct:language` ou
  `dct:conformsTo`). Les filtres ajoutés restent dans l’adresse de la page,
  si bien qu’un lien partagé les conserve, et ce navigateur les mémorise.
  **Supprimer** en retire un.

Les filtres numériques, comme **Nombre d'enregistrements**, comptent les
résultats par tranches et acceptent aussi une valeur minimale et une valeur
maximale de votre choix. Les filtres Oui/Non indiquent si une ressource
déclare ou non quelque chose (par exemple si elle concerne des données
personnelles, sur les index qui utilisent les filtres HealthDCAT-AP).

**Zone** trouve les données dont la zone recoupe un rectangle que vous
indiquez par quatre nombres : ouest, sud, est, nord (pour les Pays-Bas,
environ `3.3, 50.7, 7.3, 53.6`). Seules les ressources qui indiquent une
zone sous forme de coordonnées en ont une.

**Dates** restreint les résultats selon la date de publication ou de
dernière modification d’une ressource. **Période couverte** trouve les
données dont la couverture temporelle recoupe les années indiquées :
saisissez une année (`2010`), un mois (`2010-05`) ou une date
(`2010-05-31`), ou laissez un côté ouvert. Un jeu de données couvrant 2013
à 2025 correspond à 2012–2013 ; un jeu sans date de fin correspond à toutes
les années ultérieures. Les ressources qui n’indiquent pas de couverture
temporelle sont écartées dès que ce filtre est défini.

**Conforme au profil** conserve les enregistrements que cet index a
vérifiés par rapport à un profil de métadonnées (DCAT-AP, HealthDCAT-AP …)
et trouvés sans violation ; le résultat d’un jeu de données indique les
profils auxquels il est conforme. Sur les index de données de santé,
**Certificat de qualité** conserve les jeux de données dont le détenteur
déclare un certificat de qualité ; le certificat lui-même figure sur la
page du jeu de données. Certains index n’affichent que les enregistrements
conformes à un profil.

Pour les recherches très larges, les filtres sont comptés sur un
échantillon : ces nombres s’affichent précédés de ≈.

### Résultats

Chaque résultat indique son type, sa provenance (le FDP et le catalogue),
une description, ses thèmes, sujets et mots-clés (cliquez sur l’un d’eux
pour filtrer dessus), les formats de ses fichiers et la période couverte
par ses données. Lorsque d’autres FDP répertorient la même ressource,
**Aussi dans n autres FDP** ouvre la liste ; la page de la ressource les
affiche sous **Également publié dans**. **Regrouper les copies** affiche
une telle ressource une seule fois, sous la forme de la copie que la
recherche classe en premier ; le nombre de résultats indique alors combien
de ressources les groupes représentent. **Ouvrir à la source** mène à la
ressource sur son FDP. La page propre d’une ressource affiche chaque
valeur, ses parents et ses enfants, et ses métadonnées telles que
moissonnées (Turtle, JSON-LD ou N-Triples), ainsi que, sous **Conformité**,
son résultat pour chaque profil vérifié par l’index : ses violations, qui
enfreignent les règles d’un profil, et ses avertissements, qui sont des
recommandations.

**Copier le lien** copie l’adresse de la recherche avec ses filtres.
**Afficher en SPARQL** ouvre la même recherche sous forme de requête
SPARQL, pour l’affiner ; la requête indique où SPARQL ne peut qu’approcher
la recherche (la correspondance des mots et le classement, par exemple).

## SPARQL

La page SPARQL propose trois points d’entrée :

- **Questions** : des requêtes prêtes à l’emploi avec un formulaire pour
  leurs paramètres, comme « Datasets by theme », « Datasets by licence » ou
  « FDPs by country ». Aucune connaissance de SPARQL n’est requise.
- **Constructeur** : choisissez un type, ajoutez des conditions avec des
  suggestions tirées des données, choisissez une liste ou un décompte ; le
  SPARQL se met à jour au fur et à mesure.
- **Éditeur** : écrivez du SPARQL avec des suggestions adaptées à l’endroit
  où vous vous trouvez : des préfixes après `PREFIX`, les propriétés
  présentes dans les données là où une propriété est attendue, des classes
  après `a`, des fonctions et des variables dans `FILTER( )` et `BIND( )`,
  et `ORDER BY`, `LIMIT` et autres après le motif. Les erreurs sont
  signalées pendant la saisie ; les blocs `{ }` se replient. Ctrl/Cmd+Entrée
  exécute la requête.

Les résultats s’affichent sous forme de tableau, de graphique lorsqu’ils
comportent une catégorie et un nombre, ou bruts, et se téléchargent en
CSV, TSV, JSON ou Turtle. Les requêtes récentes restent dans votre
navigateur ; chaque requête a un lien que vous pouvez partager.

### Les données

Les métadonnées de chaque FDP répertorié forment un graphe nommé d’après
l’adresse du FDP ; le graphe par défaut les réunit tous. Le graphe
`urn:fdpindex:entries` décrit les FDP eux-mêmes : `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Les préfixes courants (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) n’ont pas besoin d’être déclarés.

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

### Depuis un programme

Le point d’accès suit le protocole SPARQL 1.1 à l’adresse
`/index/sparql` :

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Les résultats sont fournis en SPARQL JSON, XML, CSV ou TSV ; CONSTRUCT et
DESCRIBE en Turtle, N-Triples ou JSON-LD. Seules les requêtes de lecture
sont acceptées (pas de mises à jour, pas de `SERVICE`). Chaque requête
dispose d’un budget de temps et d’un nombre maximal de lignes, et les
appels anonymes sont limités par minute ; `GET /index/config` vous donne
les valeurs de cet index.

La recherche a aussi une API : `GET /index/search` accepte les mêmes
paramètres que l’adresse de la page de recherche ; [Exemples
d’API](../api.md) (en anglais) la présente, avec le flux DCAT-AP
(`/index/catalog`) et OAI-PMH (`/index/oai`) pour tout moissonner.
Consultez `/docs` sur l’index pour la liste de tous les chemins.

## Statistiques

**Statistiques** montre ce que contient l’index et comment cela évolue : FDP
listés et actifs, notices par type, les FDP ayant le plus de notices, la part
des notices conformes à chaque profil, les FDP par mois d’inscription et les
jeux de données par année de publication. Elle compte ce que voient les
visiteurs, comme la liste des FDP et la recherche. Les évolutions commencent au
premier relevé (toutes les heures ; le dernier relevé du jour est conservé) :
un nouvel index n’affiche donc une courbe qu’à partir de son deuxième jour.
Chaque graphique propose **Afficher sous forme de tableau**, et **Ouvrir comme
requête SPARQL** pose la même question en SPARQL, où vous pouvez la modifier ;
SPARQL compte par type RDF, ses chiffres peuvent donc différer légèrement.

Deux cartes montrent où sont les FDP (placés par le choix d’un administrateur,
le FDP lui-même, son éditeur, son domaine ou son serveur, dans cet ordre) et
sur quoi portent les données (la couverture spatiale des notices) ; un pays
sur la seconde carte lance la recherche de ses notices.

## Langues

L’interface est disponible en anglais, néerlandais, allemand, français,
espagnol, italien et portugais : le menu en haut de la page permet d’en
choisir une, et l’index la mémorise dans ce navigateur (au départ, il suit
les langues de votre navigateur). Les titres, descriptions et libellés
issus des métadonnées suivent la langue de l’interface lorsqu’un FDP les
fournit dans cette langue, puis les langues de repli de l’index ;
**Métadonnées en** sur la page de recherche permet de choisir une autre
langue pour ces éléments.

## Enregistrer votre FDP

Consultez la page **Enregistrer un FDP** : configurez votre FDP pour qu’il
envoie un ping à l’index, ou saisissez-y son adresse pour l’enregistrer et
voir aussitôt ce que l’index a trouvé. [Les FDP et l’index](../data-holders.md)
(en anglais) explique ce qui se passe ensuite et comment lire le rapport de
conformité de votre FDP.
