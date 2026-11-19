import { describe, expect, it } from "vitest";
import { place, variables } from "./context";

const where = (body: string) =>
  `PREFIX dct: <http://purl.org/dc/terms/>\nSELECT ?d WHERE {\n${body}`;

describe("where the cursor is in a query", () => {
  it.each([
    ["", "start"],
    ["PREFIX ", "prefix"],
    ["PREFIX dct: <http://purl.org/dc/terms/>\n", "start"],
    ["SELECT ", "projection"],
    ["SELECT DISTINCT ?a ", "projection"],
    ["SELECT ?a (", "expression"],
    ["SELECT ?a (COUNT(", "expression"],
    [where(""), "subject"],
    [where("?d "), "verb"],
    [where("?d dct:title "), "object"],
    [where("?d dct:title ?t ; "), "verb"],
    [where("?d dct:title ?t , "), "object"],
    [where("?d dct:title ?t . "), "subject"],
    [where("?d dct:title|"), "verb"],
    [where("?d dct:title/"), "verb"],
    [where("?d dcat:distribution [ "), "verb"],
    [where("?d dcat:distribution [ dcat:mediaType "), "object"],
    [where("FILTER("), "expression"],
    [where("FILTER(CONTAINS(?t, "), "expression"],
    [where("FILTER(?t > 1) "), "subject"],
    [where('FILTER CONTAINS(?t, "x") ?d '), "verb"],
    [where("BIND(STR(?d) AS ?s) ?d dct:title ?t . OPTIONAL { "), "subject"],
    [where("OPTIONAL { ?d dct:title ?t } ?d "), "verb"],
    [where('?d dct:title "a { b" ; '), "verb"],
    [where("?d dct:title ?t }\n"), "modifier"],
    [where("?d dct:title ?t } ORDER BY "), "modifier"],
    [where("?d dct:title ?t } HAVING ("), "expression"],
    ["ASK { ?d ", "verb"],
  ])("%j is a place for %s", (before, kind) => {
    expect(place(before).kind).toBe(kind);
  });

  it("knows when classes come first", () => {
    expect(place(where("?d a "))).toEqual({ kind: "object", classes: true });
    expect(place(where("?d rdf:type "))).toEqual({ kind: "object", classes: true });
    expect(place(where("?d a dcat:Dataset , "))).toEqual({ kind: "object", classes: true });
    expect(place(where("?d dct:title "))).toEqual({ kind: "object", classes: false });
  });

  it("lists the query's variables once, in order", () => {
    expect(variables("SELECT ?b ?a WHERE { ?a ?p ?b . $c ?p ?a }")).toEqual([
      "?b",
      "?a",
      "?p",
      "$c",
    ]);
  });
});
