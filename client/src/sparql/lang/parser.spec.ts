import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parser } from "./parser";

/** The positions and surroundings of syntax errors, empty when the text parses. */
function errors(text: string): string[] {
  const found: string[] = [];
  parser.parse(text).iterate({
    enter(node) {
      if (node.type.isError)
        found.push(`${node.from}: …${text.slice(node.from - 15, node.from + 15)}…`);
    },
  });
  return found;
}

function nodes(text: string): string[] {
  const names: string[] = [];
  parser.parse(text).iterate({
    enter(node) {
      names.push(node.name);
    },
  });
  return names;
}

// Every construct of SPARQL 1.1 SELECT and ASK, and the other two forms.
const VALID: Record<string, string> = {
  "prologue and star": `
    BASE <http://example.org/>
    PREFIX dcat: <http://www.w3.org/ns/dcat#>
    PREFIX : <http://example.org/ns#>
    select * where { ?s ?p ?o }`,
  "distinct, expressions and aliases": `
    SELECT DISTINCT ?d (STR(?t) AS ?title) (COUNT(DISTINCT ?x) * 2 + 1 AS ?n) WHERE {
      ?d a dcat:Dataset ; dct:title ?t , ?alt ; dcat:keyword ?x .
    } GROUP BY ?d ?t HAVING (COUNT(?x) > 1 && ?t != "") ORDER BY DESC(?n) ASC(?t) ?d
    LIMIT 10 OFFSET 20`,
  "reduced, from, from named": `
    SELECT REDUCED ?s FROM <http://a.example/g> FROM NAMED <http://b.example/g> WHERE { ?s ?p ?o }`,
  "optional, union, minus, graph, service": `
    SELECT ?s WHERE {
      { ?s a :A } UNION { ?s a :B } UNION { ?s a :C }
      OPTIONAL { ?s :label ?l FILTER(LANGMATCHES(LANG(?l), "en")) }
      MINUS { ?s :hidden true }
      GRAPH ?g { ?s :p ?o } .
      GRAPH <http://example.org/g> { ?s :q ?o2 }
      SERVICE SILENT <http://example.org/sparql> { ?s :r ?o3 }
    }`,
  "filters and built-ins": `
    SELECT ?s WHERE {
      ?s :p ?o .
      FILTER(REGEX(STR(?o), "^a.*b$", "i"))
      FILTER(?o IN (1, 2.5, 3e10, -4) || ?o NOT IN ("x", 'y'))
      FILTER(!BOUND(?x) && isIRI(?s) && STRSTARTS(LCASE(?o), "abc"))
      FILTER(COALESCE(?a, ?b, 0) >= 3 && IF(?c, 1, 2) <= 4 && ?d = <http://x/y>)
      FILTER(YEAR(NOW()) - YEAR(xsd:dateTime(?date)) < 5)
      FILTER(SUBSTR(STR(?o), 1, 10) = CONCAT("a", "b", ENCODE_FOR_URI("c d")))
      FILTER(sameTerm(?s, ?o) || ?x * -2 / +3 = 1)
      FILTER EXISTS { ?s :q ?z }
      FILTER NOT EXISTS { ?s :hidden true }
      FILTER(<http://example.org/fn>(?o, 1))
    }`,
  "bind, values, subselect": `
    SELECT ?s ?total WHERE {
      BIND(STRLEN(?name) AS ?len)
      VALUES ?s { <http://a> :b UNDEF }
      VALUES (?x ?y) { (1 "a"@en) (UNDEF 2) () }
      { SELECT ?s (COUNT(*) AS ?total) WHERE { ?s ?p ?o } GROUP BY ?s }
    }
    VALUES ?total { 1 2 }`,
  "property paths": `
    SELECT * WHERE {
      ?s dct:title|dct:description|dcat:keyword ?t .
      ?s dcat:distribution/dcat:mediaType ?m .
      ?s ^dcat:dataset ?catalog .
      ?s (dct:isPartOf)+ ?root .
      ?s :p* ?x ; :q? ?y .
      ?s !(rdf:type|^:hidden) ?z .
      ?s !rdf:type ?w .
      ?s (:a/:b)|^:c ?v .
    }`,
  "terms: literals, blank nodes, collections": `
    SELECT * WHERE {
      ?s :string "plain", 'single', """long
      "quoted" text""", '''long ''single''' ;
         :lang "Gezondheid"@nl, "Health"@en-GB ;
         :typed "2024-01-01"^^xsd:date, "5"^^<http://www.w3.org/2001/XMLSchema#int> ;
         :number 1, -2, +3.5, .5, 1e3, 2.0E-2 ;
         :bool true, false ;
         :blank _:b1, [], [ :p :o ; :q "r" ] ;
         :list (1 ?x "three") , () .
      [ :p ?o ] :q ?r .
      (1 2) :p ?o .
      ?s a :Thing .
      $dollar :p ?o .
    }`,
  aggregates: `
    SELECT (SUM(?n) AS ?sum) (AVG(?n) AS ?avg) (MIN(?n) AS ?min) (MAX(?n) AS ?max)
      (SAMPLE(?n) AS ?one) (GROUP_CONCAT(DISTINCT ?l ; SEPARATOR = ", ") AS ?all)
      (COUNT(*) AS ?count) (COUNT(DISTINCT *) AS ?distinct)
    WHERE { ?s :n ?n ; :l ?l }
    GROUP BY (YEAR(?d) AS ?year) STR(?s)`,
  "comments and lower-case keywords": `
    # a comment
    prefix dct: <http://purl.org/dc/terms/>  # another
    select ?t where {
      ?d dct:title ?t . # trailing
      filter(contains(lcase(?t), "cancer"))
    } order by ?t limit 5`,
  ask: `ASK { ?s a dcat:Dataset ; dct:title ?t FILTER(CONTAINS(?t, "x")) }`,
  "ask with from and where": `ASK FROM <http://g> WHERE { ?s ?p ?o }`,
  construct: `
    CONSTRUCT { ?d dct:title ?t . ?d a dcat:Dataset } WHERE { ?d dct:title ?t }`,
  "construct where": `CONSTRUCT WHERE { ?d dct:title ?t }`,
  describe: `DESCRIBE ?d <http://example.org/x> WHERE { ?d a dcat:Dataset }`,
  "describe star": `DESCRIBE *`,
  "empty group": `SELECT * WHERE { }`,
  "where omitted": `SELECT ?s { ?s ?p ?o }`,
  "prefixed names with dots and escapes": `
    SELECT * WHERE { ?s ex:a.b ?o ; ex:c\\-d ?o2 ; doi:10.5072 ?o3 ; :%41 ?o4 . }`,
};

const INVALID: Record<string, string> = {
  "unclosed group": `SELECT * WHERE { ?s ?p ?o`,
  "missing object": `SELECT * WHERE { ?s ?p }`,
  "bad clause order": `SELECT * WHERE { ?s ?p ?o } LIMIT 1 ORDER BY ?s`,
  update: `INSERT DATA { <a> <b> <c> }`,
  "two triples without a dot": `SELECT * WHERE { ?s ?p ?o ?a ?b ?c }`,
};

describe("the SPARQL grammar", () => {
  for (const [name, query] of Object.entries(VALID)) {
    it(`parses ${name}`, () => {
      expect(errors(query)).toEqual([]);
    });
  }

  for (const [name, query] of Object.entries(INVALID)) {
    it(`flags ${name}`, () => {
      expect(errors(query).length).toBeGreaterThan(0);
    });
  }

  it("parses every query of the bundled library", () => {
    // Tests run in client/; the library is the server's.
    const yaml = readFileSync(
      resolve(process.cwd(), "../server/src/fdpindex/sparql/examples.default.yaml"),
      "utf8",
    );
    const queries: string[] = [];
    const lines = yaml.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (!/^\s+query: \|/.test(lines[i]!)) continue;
      const block: string[] = [];
      const indent = (lines[i + 1]!.match(/^\s*/) ?? [""])[0].length;
      for (
        i += 1;
        i < lines.length && (!lines[i]!.trim() || lines[i]!.search(/\S/) >= indent);
        i++
      ) {
        block.push(lines[i]!.slice(indent));
      }
      i -= 1;
      queries.push(block.join("\n"));
    }
    expect(queries.length).toBeGreaterThan(10);
    for (const query of queries) expect(errors(query), query).toEqual([]);
  });

  it("names the parts that highlighting, folding and completion use", () => {
    const names = nodes(`PREFIX dct: <http://purl.org/dc/terms/>
      SELECT ?t WHERE { ?d a dcat:Dataset ; dct:title ?t . FILTER(STRLEN(?t) > 3) }`);
    for (const name of [
      "PrefixDecl",
      "SelectClause",
      "GroupGraphPattern",
      "PropertyList",
      "Path",
      "A",
      "PrefixedName",
      "Var",
      "Filter",
      "BuiltInCall",
      "FunctionName",
      "CompareOp",
    ]) {
      expect(names).toContain(name);
    }
  });
});
