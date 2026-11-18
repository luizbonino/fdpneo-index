import { describe, expect, it } from "vitest";
import {
  decodeModel,
  emptyModel,
  encodeModel,
  generate,
  variableFor,
  type BuilderModel,
} from "./builder";

const DCAT = "http://www.w3.org/ns/dcat#";
const DCT = "http://purl.org/dc/terms/";

function model(change: Partial<BuilderModel>): BuilderModel {
  return { ...emptyModel(), ...change };
}

describe("query builder", () => {
  it("lists resources of a type with their title and FDP", () => {
    const query = generate(emptyModel());
    expect(query).toContain("PREFIX dcat: <http://www.w3.org/ns/dcat#>");
    expect(query).toContain("PREFIX dct: <http://purl.org/dc/terms/>");
    expect(query).toContain("?item a dcat:Dataset .");
    expect(query).toContain("SELECT ?item (SAMPLE(?t) AS ?title) ?fdp");
    expect(query).toContain("GROUP BY ?item ?fdp");
    expect(query).toMatch(/LIMIT 100\n$/);
  });

  it("matches IRIs with VALUES and text with a lower-cased CONTAINS", () => {
    const query = generate(
      model({
        conditions: [
          {
            property: `${DCAT}theme`,
            operator: "is",
            values: ["http://x.example/HEAL"],
            kind: "uri",
          },
          { property: `${DCAT}keyword`, operator: "contains", values: ["Cancer"], kind: "literal" },
        ],
      }),
    );
    expect(query).toContain("VALUES ?theme { <http://x.example/HEAL> }");
    expect(query).toContain("?item dcat:theme ?theme .");
    expect(query).toContain('FILTER(CONTAINS(LCASE(STR(?keyword)), "cancer"))');
  });

  it("escapes text and refuses IRIs that could break out", () => {
    const query = generate(
      model({
        conditions: [
          {
            property: `${DCT}title`,
            operator: "contains",
            values: ['a") } DROP ALL #'],
            kind: "literal",
          },
        ],
      }),
    );
    expect(query).toContain('"a\\") } drop all #"');
    expect(() =>
      generate(
        model({
          conditions: [
            { property: `${DCAT}theme`, operator: "is", values: ["http://x> } DROP"], kind: "uri" },
          ],
        }),
      ),
    ).toThrow(/valid IRI/);
  });

  it("follows one hop, including inverse ones", () => {
    const query = generate(
      model({
        conditions: [
          {
            via: `${DCAT}distribution`,
            property: `${DCAT}mediaType`,
            operator: "contains",
            values: ["csv"],
            kind: "literal",
          },
          {
            via: `^${DCAT}dataset`,
            property: `${DCT}title`,
            operator: "exists",
            values: [],
            kind: "literal",
          },
        ],
      }),
    );
    expect(query).toContain("?item dcat:distribution/dcat:mediaType ?mediaType .");
    expect(query).toContain("?item ^dcat:dataset/dct:title ?title2 .");
  });

  it("filters missing properties, dates and numbers outside the graph pattern", () => {
    const query = generate(
      model({
        conditions: [
          { property: `${DCT}license`, operator: "missing", values: [], kind: "literal" },
          { property: `${DCT}issued`, operator: "after", values: ["2024-01-01"], kind: "literal" },
          { property: `${DCAT}byteSize`, operator: "greater", values: ["1000"], kind: "literal" },
        ],
      }),
    );
    expect(query).toContain("FILTER NOT EXISTS { ?item dct:license ?any }");
    expect(query).toContain('FILTER(STR(?issued) >= "2024-01-01")');
    expect(query).toContain("FILTER(xsd:decimal(?byteSize) > 1000)");
    expect(query).toContain("PREFIX xsd:");
  });

  it("counts per value or per FDP", () => {
    const perTheme = generate(model({ countBy: `${DCAT}theme` }));
    expect(perTheme).toContain("SELECT ?theme (COUNT(DISTINCT ?item) AS ?count)");
    expect(perTheme).toContain("GROUP BY ?theme");
    const perFdp = generate(model({ countBy: "fdp", fdp: "https://fdp.example.org" }));
    expect(perFdp).toContain("VALUES ?fdp { <https://fdp.example.org> }");
    expect(perFdp).toContain("GROUP BY ?fdp");
  });

  it("adds columns as samples and skips incomplete conditions", () => {
    const query = generate(
      model({
        columns: [`${DCAT}keyword`, `${DCT}title`],
        conditions: [{ property: `${DCAT}theme`, operator: "is", values: [""], kind: "uri" }],
      }),
    );
    expect(query).toContain("(SAMPLE(?_keyword) AS ?keyword)");
    expect(query).not.toContain("dcat:theme");
  });

  it("names variables uniquely", () => {
    const taken = new Set<string>(["title"]);
    expect(variableFor(`${DCT}title`, taken)).toBe("title2");
    expect(variableFor("http://x.example/2nd-thing", taken)).toBe("_2nd_thing");
  });

  it("round-trips the model through the URL", () => {
    const original = model({ fdp: "https://fdp.example.org/ü", columns: [`${DCAT}keyword`] });
    expect(decodeModel(encodeModel(original))).toEqual(original);
    expect(decodeModel("not base64!")).toBeNull();
  });
});
