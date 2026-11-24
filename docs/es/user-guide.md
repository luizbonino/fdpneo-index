# Uso del FDPneo Index

*Traducción de la [guía en inglés](../user-guide.md).*

Para quienes buscan datos: qué contiene el índice y cómo encontrar cosas
en él. Nada de lo que se describe en esta página requiere una cuenta.

## Qué contiene el índice

Los FAIR Data Points (FDP) publican metadatos sobre datos: catálogos,
conjuntos de datos, sus distribuciones (archivos y API) y servicios de datos.
Se registran en el índice enviándole un ping. A partir de ahí, el índice
comprueba cada FDP periódicamente y recolecta sus metadatos, de modo que se
puede buscar en todos ellos desde un solo lugar.

- **Directorio** (la página de inicio): todos los FDP que lista el índice,
  con su estado. *Activo* significa que el índice lo alcanzó recientemente;
  *Inaccesible* y *Metadatos no válidos* significan que fallaron las últimas
  comprobaciones; *Sin actividad reciente* significa que hace tiempo que no
  se comprueba.
- **Buscar**: los metadatos recolectados de todos los FDP de la lista.
- **SPARQL**: los mismos metadatos como grafo de conocimiento, para preguntas
  precisas.

## Buscar

Se escriben palabras en el cuadro de búsqueda. La búsqueda mira en títulos,
descripciones, palabras clave, editores y nombres de materias, en los idiomas
propios de los metadatos, y encuentra otras formas de una palabra (buscar
*registry* encuentra *registries*).

| Lo que se escribe | Lo que se encuentra |
|---|---|
| `breast cancer` | recursos con ambas palabras |
| `"lung function"` | esa frase exacta |
| `cancer or tumour` | cualquiera de las dos palabras |
| `cancer -lung` | *cancer* sin *lung* |
| `title:registry` | *registry* en el título (también `description:`) |
| `keyword:asthma` | recursos con esa palabra clave |
| `theme:Health`, `theme:HEAL` | un tema por su nombre en cualquier idioma, su código o su IRI |
| `publisher:"Alpha University"` | editores cuyo nombre contiene esas palabras |
| `identifier:10.5072/abc` | un DOI u otro identificador |
| `-keyword:mouse` | sin esa palabra clave |

Cualquier campo que tenga el índice funciona igual (`format:csv`,
`licence:…`, `subject:…`). Un prefijo de campo actúa como filtro: sus valores
aparecen marcados en los filtros del lateral.

Una búsqueda sin resultados propone una corrección cuando esta sí encuentra
algo: *brest cancr* ofrece **Búsqueda sugerida: breast cancer**.

Cuando el índice resuelve los nombres de las materias (una decisión del
administrador), una búsqueda también encuentra materias por sus nombres en
otros idiomas y por términos más amplios: *borstkanker* o *Brustkrebs*
encuentran conjuntos de datos etiquetados con el elemento de Wikidata para el
cáncer de mama, y *neoplasms* encuentra los etiquetados con *Breast
Neoplasms*.

Las pestañas bajo el cuadro limitan los resultados a conjuntos de datos,
catálogos, servicios de datos o FDP.

### Filtros

Los filtros del lateral cuentan, para cada valor, cuántos resultados daría.
Marcar dos valores de un mismo filtro muestra los recursos que tienen uno u
otro; las marcas en filtros distintos deben cumplirse todas. Cada filtro
activo aparece como una etiqueta encima de los resultados; al hacer clic en
ella, se quita.

- **Tema**: los 13 grandes temas de datos de la UE, como Salud.
- **Materia**: temas específicos de vocabularios como MeSH, Wikidata o EDAM.
  El mismo tema procedente de dos vocabularios es una sola entrada cuando un
  vocabulario indica que coinciden (*dementia* de Wikidata es *Dementia* de
  MeSH).
  **Explorar Materia como árbol** muestra cada materia bajo las más amplias;
  marcar una ahí la encuentra a ella y a todo lo más específico (*Neoplasms
  by Site* incluye *Breast Neoplasms*).
- **Palabra clave**, **Editor**, **Formato**, **Licencia**, **Derechos de
  acceso**, **FAIR Data Point**, y más en **Más filtros**, entre ellos
  **Cobertura geográfica**: los lugares que abarcan los datos. Un país es una
  sola entrada, lo nombre como lo nombre un FDP (la lista de la UE, GeoNames
  o un código como `NL`).
- **+ Añadir un filtro**: cualquier propiedad presente en los metadatos, las
  más usadas primero (por ejemplo `dct:language` o `dct:conformsTo`). Los
  filtros añadidos se guardan en la dirección de la página, de modo que un
  enlace compartido los incluye, y este navegador los recuerda. **Quitar**
  elimina uno.

Los filtros numéricos, como **Número de registros**, cuentan los resultados
por intervalos y admiten también un valor mínimo y uno máximo propios. Los
filtros Sí/No indican si un recurso declara algo o no (por ejemplo, si
concierne a datos personales, en los índices que usan los filtros de
HealthDCAT-AP).

**Zona** encuentra datos cuya zona se solapa con un recuadro dado como cuatro
números: oeste, sur, este, norte (para los Países Bajos, aproximadamente
`3.3, 50.7, 7.3, 53.6`). Solo tienen zona los recursos que la declaran como
coordenadas.

**Fechas** filtra por cuándo se publicó o se modificó por última vez un
recurso. **Periodo cubierto** encuentra datos cuya cobertura temporal se
solapa con los años indicados: se escribe un año (`2010`), un mes (`2010-05`)
o una fecha (`2010-05-31`), o se deja abierto uno de los extremos. Un
conjunto de datos que abarca de 2013 a 2025 coincide con 2012–2013; uno sin
fecha de fin coincide con todos los años posteriores. Los recursos que no
declaran una cobertura temporal quedan fuera en cuanto se fija este filtro.

**Cumple el perfil** conserva los registros que este índice ha comprobado con
un perfil de metadatos (DCAT-AP, HealthDCAT-AP …) sin encontrar infracciones;
el resultado de un conjunto de datos indica qué perfiles cumple. En los
índices de datos de salud, **Certificado de calidad** conserva los conjuntos
de datos cuyo titular declara un certificado de calidad; el certificado en sí
está en la página del conjunto de datos. Algunos índices solo muestran
registros que cumplen un perfil.

Las búsquedas muy amplias cuentan sus filtros sobre una muestra: esos
recuentos aparecen con ≈.

### Resultados

Cada resultado muestra su tipo, su procedencia (el FDP y el catálogo), una
descripción, sus temas, materias y palabras clave (al hacer clic en uno, se
filtra por él), los formatos de sus archivos y el periodo que abarcan sus
datos. Cuando otros FDP listan el mismo recurso, **También en n FDP más**
abre la lista; la página del recurso los muestra en **También publicado en**.
**Agrupar copias** muestra ese recurso una sola vez, como la copia que la
búsqueda sitúa primero; el recuento indica entonces cuántos recursos
representan los grupos. **Abrir en el origen** lleva al recurso en su FDP. La
página propia de un recurso muestra todos sus valores, sus padres e hijos y
sus metadatos tal como se recolectaron (Turtle, JSON-LD o N-Triples), y, en
**Conformidad**, cómo se ajusta a cada perfil que comprueba el índice: sus
infracciones, que incumplen las reglas de un perfil, y sus advertencias, que
son recomendaciones.

**Copiar enlace** copia la dirección de la búsqueda con sus filtros.
**Mostrar como SPARQL** abre la misma búsqueda como consulta SPARQL, para
refinarla; la consulta indica dónde SPARQL solo puede aproximar la búsqueda
(por ejemplo, en la coincidencia de palabras y la ordenación).

## SPARQL

La página SPARQL ofrece tres vías de entrada:

- **Preguntas**: consultas ya preparadas con un formulario para sus
  parámetros, como "Datasets by theme", "Datasets by licence" o "FDPs by
  country". No hace falta saber SPARQL.
- **Constructor**: elegir un tipo, añadir condiciones con sugerencias a
  partir de los datos y elegir una lista o un recuento; el SPARQL se
  actualiza sobre la marcha.
- **Editor**: escribir SPARQL con sugerencias adaptadas a la posición del
  cursor: prefijos después de `PREFIX`, las propiedades de los datos donde va
  una propiedad, clases después de `a`, funciones y variables dentro de
  `FILTER( )` y `BIND( )`, y `ORDER BY`, `LIMIT` y similares después del
  patrón. Los errores se marcan mientras se escribe; los bloques `{ }` se
  pliegan. Ctrl/Cmd+Enter ejecuta la consulta.

Los resultados se muestran como tabla, como gráfico cuando tienen una
categoría y un número, o en bruto, y se descargan como CSV, TSV, JSON o
Turtle. Las consultas recientes se guardan en el navegador; cada consulta
tiene un enlace que se puede compartir.

### Los datos

Los metadatos de cada FDP de la lista forman un grafo con nombre, cuyo nombre
es la dirección del FDP; el grafo por defecto es la unión de todos ellos. El
grafo `urn:fdpindex:entries` describe los propios FDP: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Los prefijos habituales (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) no necesitan declaración.

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

### Desde un programa

El punto de acceso sigue el protocolo SPARQL 1.1 en `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Los resultados llegan como SPARQL JSON, XML, CSV o TSV; los de CONSTRUCT y
DESCRIBE, como Turtle, N-Triples o JSON-LD. Solo se aceptan consultas (ni
actualizaciones ni `SERVICE`). Cada consulta tiene un tiempo máximo y un
límite de filas, y las llamadas anónimas, un límite de frecuencia por minuto;
`GET /index/config` indica los valores de este índice.

La búsqueda también tiene una API: `GET /index/search` acepta los mismos
parámetros que la dirección de la página de búsqueda; [Ejemplos de la
API](../api.md) (en inglés) la muestra, junto con el feed DCAT-AP
(`/index/catalog`) y OAI-PMH (`/index/oai`) para recolectarlo todo. En
`/docs` del índice figuran todas las rutas.

## Estadísticas

**Estadísticas** muestra lo que contiene el índice y cómo cambia: FDP en la
lista y activos, registros por tipo, los FDP con más registros, la proporción
de registros que cumple cada perfil, los FDP por mes de registro y los
conjuntos de datos por año de publicación. Cuenta lo que ven los visitantes,
igual que la lista de FDP y la búsqueda. La evolución empieza con el primer
recuento (cada hora; se conserva el último del día), así que un índice nuevo
solo muestra una línea a partir de su segundo día. Cada gráfico ofrece **Ver
como tabla**, y **Abrir como consulta SPARQL** plantea la misma pregunta en
SPARQL, donde se puede modificar; SPARQL cuenta por tipo RDF, por lo que sus
cifras pueden diferir un poco.

Dos mapas muestran dónde están los FDP (situados por la elección de un
administrador, el propio FDP, su editor, su dominio o su servidor, en ese
orden) y de dónde tratan los datos (la cobertura espacial de los registros);
un país del segundo mapa busca sus registros.

## Idiomas

La interfaz está disponible en inglés, neerlandés, alemán, francés, español,
italiano y portugués: el menú de la parte superior permite elegir uno, y el
índice lo recuerda en este navegador (al principio sigue los idiomas del
navegador). Los títulos, descripciones y etiquetas de los metadatos siguen el
idioma de la interfaz cuando un FDP los ofrece en él y, si no, los idiomas de
reserva del índice; **Metadatos en**, en la página de búsqueda, permite
elegir otro idioma para ellos.

## Registrar un FDP propio

Ver la página **Registrar un FDP**: configurar el FDP para que envíe un ping
al índice, o introducir allí su dirección para registrarlo y ver al instante
lo que el índice ha encontrado. [Los FDP y el índice](../data-holders.md) (en
inglés) explica lo que ocurre después y cómo leer el informe de conformidad
del FDP.
