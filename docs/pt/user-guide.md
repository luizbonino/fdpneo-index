# Utilização do FDPneo Index

*Tradução do [guia em inglês](../user-guide.md).*

Para quem procura dados: o que o índice contém e como encontrar nele o que se
procura. Nada do que esta página descreve exige uma conta.

## O que o índice contém

Os FAIR Data Points (FDP) publicam metadados sobre dados: catálogos,
conjuntos de dados, as respetivas distribuições (ficheiros e API) e serviços
de dados. Registam-se no índice enviando-lhe um ping. O índice verifica
depois cada FDP regularmente e recolhe os seus metadados, de modo que é
possível pesquisar em todos num só lugar.

- **Diretório** (a página inicial): todos os FDP listados pelo índice, com o
  respetivo estado. *Ativo* significa que o índice o contactou recentemente;
  *Inacessível* e *Metadados inválidos* significam que as últimas
  verificações falharam; *Não visto recentemente* significa que não é
  verificado há algum tempo.
- **Pesquisa**: os metadados recolhidos de todos os FDP listados.
- **SPARQL**: os mesmos metadados como grafo de conhecimento, para perguntas
  precisas.

## Pesquisar

Escrevem-se palavras na caixa de pesquisa. A pesquisa procura em títulos,
descrições, palavras-chave, editores e nomes de assuntos, nas próprias
línguas dos metadados, e encontra outras formas de uma palavra (pesquisar
*registry* encontra *registries*).

| O que se escreve | O que se encontra |
|---|---|
| `breast cancer` | recursos com ambas as palavras |
| `"lung function"` | exatamente essa expressão |
| `cancer or tumour` | qualquer uma das palavras |
| `cancer -lung` | *cancer* sem *lung* |
| `title:registry` | *registry* no título (também `description:`) |
| `keyword:asthma` | recursos com essa palavra-chave |
| `theme:Health`, `theme:HEAL` | um tema pelo nome em qualquer língua, pelo código ou pelo IRI |
| `publisher:"Alpha University"` | editores cujo nome contém essas palavras |
| `identifier:10.5072/abc` | um DOI ou outro identificador |
| `-keyword:mouse` | sem essa palavra-chave |

Qualquer campo que o índice tenha funciona da mesma forma (`format:csv`,
`licence:…`, `subject:…`). Um prefixo de campo funciona como filtro: os seus
valores aparecem assinalados nos filtros laterais.

Uma pesquisa sem resultados propõe uma correção quando esta encontra alguma
coisa: para *brest cancr* é sugerido **Quis dizer breast cancer**.

Quando o índice resolve os nomes dos assuntos (uma opção do administrador),
uma pesquisa encontra também assuntos pelos seus nomes noutras línguas e por
termos mais amplos: *borstkanker* ou *Brustkrebs* encontram conjuntos de
dados etiquetados com o item do Wikidata para o cancro da mama, e *neoplasms*
encontra os etiquetados *Breast Neoplasms*.

Os separadores abaixo da caixa restringem os resultados a conjuntos de dados,
catálogos, serviços de dados ou FDP.

### Filtros

Os filtros laterais contam, para cada valor, quantos resultados daria.
Assinalar dois valores do mesmo filtro mostra os recursos que tenham um ou
outro; os valores assinalados em filtros diferentes têm de se verificar
todos. Cada filtro ativo aparece como uma etiqueta acima dos resultados;
clicar nela remove-o.

- **Tema**: os 13 grandes temas de dados da UE, como Saúde.
- **Assunto**: tópicos específicos de vocabulários como MeSH, Wikidata ou
  EDAM. O mesmo tópico em dois vocabulários é uma só entrada quando um
  vocabulário indica que correspondem (*dementia* do Wikidata é *Dementia*
  do MeSH).
  **Navegar em Assunto como árvore** mostra cada assunto sob os mais amplos;
  assinalar um aí encontra esse assunto e tudo o que é mais específico
  (*Neoplasms by Site* inclui *Breast Neoplasms*).
- **Palavra-chave**, **Editor**, **Formato**, **Licença**, **Direitos de
  acesso**, **FAIR Data Point**, e mais em **Mais filtros**, entre eles
  **Cobertura geográfica**: os lugares abrangidos pelos dados. Um país é uma
  só entrada, seja qual for a forma como um FDP o designa (a lista da UE,
  GeoNames ou um código como `NL`).
- **+ Adicionar um filtro**: qualquer propriedade encontrada nos metadados,
  as mais usadas primeiro (por exemplo `dct:language` ou `dct:conformsTo`).
  Os filtros adicionados ficam no endereço da página, pelo que uma ligação
  partilhada os inclui, e este navegador memoriza-os. **Remover** retira um.

Os filtros numéricos, como **Número de registos**, contam os resultados por
intervalos e aceitam também um valor mínimo e um valor máximo à escolha. Os
filtros Sim/Não indicam se um recurso declara ou não alguma coisa (por
exemplo, se diz respeito a dados pessoais, nos índices que usam os filtros
HealthDCAT-AP).

**Área** encontra dados cuja área se sobrepõe a um retângulo indicado por
quatro números: oeste, sul, este, norte (para os Países Baixos, cerca de
`3.3, 50.7, 7.3, 53.6`). Só têm área os recursos que a declaram como
coordenadas.

**Datas** restringe pela data em que um recurso foi publicado ou modificado
pela última vez. **Período abrangido** encontra dados cuja cobertura temporal
se sobrepõe aos anos indicados: escreve-se um ano (`2010`), um mês
(`2010-05`) ou uma data (`2010-05-31`), ou deixa-se um dos lados em aberto.
Um conjunto de dados que abrange de 2013 a 2025 corresponde a 2012–2013; um
sem data de fim corresponde a todos os anos posteriores. Os recursos que não
declaram cobertura temporal ficam excluídos assim que este filtro é
definido.

**Cumpre o perfil** mantém os registos que este índice verificou face a um
perfil de metadados (DCAT-AP, HealthDCAT-AP …) sem encontrar violações; o
resultado de um conjunto de dados indica que perfis cumpre. Nos índices de
dados de saúde, **Certificado de qualidade** mantém os conjuntos de dados
cujo detentor declara um certificado de qualidade; o próprio certificado
está na página do conjunto de dados. Alguns índices só mostram registos que
cumprem um perfil.

As pesquisas muito amplas contam os filtros sobre uma amostra: essas
contagens aparecem com ≈.

### Resultados

Cada resultado mostra o tipo, a proveniência (o FDP e o catálogo), uma
descrição, os temas, assuntos e palavras-chave (clicar num deles filtra por
esse valor), os formatos dos ficheiros e o período abrangido pelos dados.
Quando outros FDP listam o mesmo recurso, **Também em n outros FDP** abre a
lista; a página do recurso mostra-os em **Também publicado em**. **Agrupar
cópias** mostra esse recurso uma só vez, como a cópia que a pesquisa coloca
em primeiro lugar; a contagem indica então quantos recursos os grupos
representam. **Abrir na origem** leva ao recurso no seu FDP. A página própria
de um recurso mostra todos os valores, os recursos pai e filhos, os
metadados tal como foram recolhidos (Turtle, JSON-LD ou N-Triples) e, em
**Conformidade**, como se comporta face a cada perfil que o índice verifica:
as violações, que infringem as regras de um perfil, e os avisos, que são
recomendações.

**Copiar ligação** copia o endereço da pesquisa com os filtros. **Mostrar
como SPARQL** abre a mesma pesquisa como consulta SPARQL, para a refinar; a
consulta indica onde o SPARQL só consegue aproximar a pesquisa (por exemplo,
na correspondência de palavras e na ordenação).

## SPARQL

A página SPARQL oferece três formas de entrada:

- **Perguntas**: consultas prontas com um formulário para os parâmetros,
  como "Datasets by theme", "Datasets by licence" ou "FDPs by country". Não
  é preciso saber SPARQL.
- **Construtor**: escolher um tipo, acrescentar condições com sugestões a
  partir dos dados, escolher uma lista ou uma contagem; o SPARQL atualiza-se
  à medida que se avança.
- **Editor**: escrever SPARQL com sugestões adequadas à posição do cursor:
  prefixos depois de `PREFIX`, as propriedades dos dados onde vai uma
  propriedade, classes depois de `a`, funções e variáveis dentro de
  `FILTER( )` e `BIND( )`, e `ORDER BY`, `LIMIT` e semelhantes depois do
  padrão. Os erros são assinalados durante a escrita; os blocos `{ }` podem
  ser recolhidos. Ctrl/Cmd+Enter executa a consulta.

Os resultados são apresentados como tabela, como gráfico quando têm uma
categoria e um número, ou em bruto, e podem ser transferidos como CSV, TSV,
JSON ou Turtle. As consultas recentes ficam no navegador; cada consulta tem
uma ligação que pode ser partilhada.

### Os dados

Os metadados de cada FDP listado formam um grafo nomeado, cujo nome é o
endereço do FDP; o grafo predefinido é o conjunto de todos eles. O grafo
`urn:fdpindex:entries` descreve os próprios FDP: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Os prefixos comuns (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) não precisam de declaração.

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

### A partir de um programa

O ponto de acesso segue o protocolo SPARQL 1.1 em `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Os resultados chegam como SPARQL JSON, XML, CSV ou TSV; os de CONSTRUCT e
DESCRIBE como Turtle, N-Triples ou JSON-LD. Só são aceites consultas (nem
atualizações nem `SERVICE`). Cada consulta tem um tempo máximo e um limite de
linhas, e os pedidos anónimos um limite de frequência por minuto;
`GET /index/config` indica os valores deste índice.

A pesquisa também tem uma API: `GET /index/search` aceita os mesmos
parâmetros que o endereço da página de pesquisa; [Exemplos da API](../api.md)
(em inglês) mostra-a, juntamente com o feed DCAT-AP (`/index/catalog`) e o
OAI-PMH (`/index/oai`) para recolher tudo. Todos os caminhos estão em `/docs`
no índice.

## Idiomas

A interface está disponível em inglês, neerlandês, alemão, francês,
espanhol, italiano e português: o menu no topo permite escolher um, e o
índice memoriza-o neste navegador (de início segue os idiomas do navegador).
Os títulos, descrições e etiquetas dos metadados seguem o idioma da
interface quando um FDP os fornece nesse idioma e, caso contrário, os
idiomas de recurso do índice; **Metadados em**, na página de pesquisa,
permite escolher outro idioma para eles.

## Registar um FDP próprio

Consultar a página **Registar um FDP**: configurar o FDP para enviar um ping
ao índice, ou introduzir aí o seu endereço para o registar e ver de imediato
o que o índice encontrou. [Os FDP e o índice](../data-holders.md) (em
inglês) explica o que acontece a seguir e como ler o relatório de
conformidade do FDP.
