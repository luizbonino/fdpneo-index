import { describe, expect, it } from "vitest";
import {
  chartShape,
  curie,
  isLinkable,
  shortIri,
  toCsv,
  toMarkdown,
  toTable,
  toTsv,
} from "./results";
import type { SelectResults } from "./api";

const INT = "http://www.w3.org/2001/XMLSchema#integer";
const results: SelectResults = {
  head: { vars: ["theme", "n"] },
  results: {
    bindings: [
      {
        theme: { type: "uri", value: "http://www.w3.org/ns/dcat#Dataset" },
        n: { type: "literal", value: "12", datatype: INT },
      },
      {
        theme: { type: "literal", value: 'a, "b"', "xml:lang": "en" },
        n: { type: "literal", value: "3", datatype: INT },
      },
    ],
  },
};

describe("results", () => {
  it("shortens IRIs", () => {
    expect(curie("http://purl.org/dc/terms/title")).toBe("dct:title");
    expect(curie("http://example.org/x")).toBeNull();
    expect(shortIri("https://fdp.example.org/catalog/abc")).toBe("fdp.example.org/…/abc");
    expect(shortIri("https://fdp.example.org/")).toBe("fdp.example.org");
  });

  it("builds a table with numeric sort keys and detects a chart", () => {
    const table = toTable(results);
    expect(table.rows[0]?.[1]?.sortKey).toBe(12);
    expect(chartShape(table)).toEqual({ label: 0, value: 1 });
    expect(chartShape({ columns: ["a"], rows: table.rows.map((r) => [r[0]!]) })).toBeNull();
  });

  it("exports CSV and Markdown", () => {
    const table = toTable(results);
    expect(toCsv(table)).toBe(
      'theme,n\r\nhttp://www.w3.org/ns/dcat#Dataset,12\r\n"a, ""b""",3\r\n',
    );
    expect(toMarkdown(table).split("\n")[1]).toBe("| --- | --- |");
  });
});

describe("untrusted values", () => {
  it("links only http(s) IRIs", () => {
    expect(isLinkable("https://fdp.example.org/x")).toBe(true);
    expect(isLinkable("javascript:alert(1)")).toBe(false);
    expect(isLinkable("data:text/html,<b>x</b>")).toBe(false);
    expect(isLinkable("urn:fdpindex:entries")).toBe(false);
  });

  it("neutralises spreadsheet formulas in CSV and TSV, but not numbers", () => {
    const table = toTable({
      head: { vars: ["v"] },
      results: {
        bindings: [
          { v: { type: "literal", value: '=HYPERLINK("http://evil","x")' } },
          { v: { type: "literal", value: "-5", datatype: INT } },
          { v: { type: "literal", value: "@SUM(A1)" } },
        ],
      },
    });
    expect(toCsv(table)).toBe(`v\r\n"'=HYPERLINK(""http://evil"",""x"")"\r\n-5\r\n'@SUM(A1)\r\n`);
    expect(toTsv(table).split("\n")[1]).toBe(`'=HYPERLINK("http://evil","x")`);
  });
});
