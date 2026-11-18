/**
 * Turning SPARQL JSON results into something to show: cells with display text,
 * chart detection, CSV/Markdown export, and short forms of IRIs.
 */

import type { SelectResults, Term } from "./api";

export const KNOWN_PREFIXES: Record<string, string> = {
  rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  rdfs: "http://www.w3.org/2000/01/rdf-schema#",
  xsd: "http://www.w3.org/2001/XMLSchema#",
  owl: "http://www.w3.org/2002/07/owl#",
  dcat: "http://www.w3.org/ns/dcat#",
  dct: "http://purl.org/dc/terms/",
  foaf: "http://xmlns.com/foaf/0.1/",
  vcard: "http://www.w3.org/2006/vcard/ns#",
  skos: "http://www.w3.org/2004/02/skos/core#",
  prov: "http://www.w3.org/ns/prov#",
  odrl: "http://www.w3.org/ns/odrl/2/",
  ldp: "http://www.w3.org/ns/ldp#",
  void: "http://rdfs.org/ns/void#",
  sh: "http://www.w3.org/ns/shacl#",
  schema: "http://schema.org/",
  adms: "http://www.w3.org/ns/adms#",
  locn: "http://www.w3.org/ns/locn#",
  time: "http://www.w3.org/2006/time#",
  "fdp-o": "https://w3id.org/fdp/fdp-o#",
  r3d: "http://www.re3data.org/schema/3-0#",
  fdpindex: "urn:fdpindex:",
};

const NUMERIC = new Set(
  ["integer", "decimal", "double", "float", "int", "long", "short", "nonNegativeInteger"].map(
    (t) => `http://www.w3.org/2001/XMLSchema#${t}`,
  ),
);

/** `dcat:Dataset` when the namespace is known, else null. */
export function curie(
  iri: string,
  prefixes: Record<string, string> = KNOWN_PREFIXES,
): string | null {
  let best: [string, string] | null = null;
  for (const [prefix, ns] of Object.entries(prefixes)) {
    if (prefix === "dcterms") continue;
    if (iri.startsWith(ns) && (!best || ns.length > best[1].length)) best = [prefix, ns];
  }
  if (!best) return null;
  const local = iri.slice(best[1].length);
  return /^[\w.-]+$/.test(local) ? `${best[0]}:${local}` : null;
}

/** A short, readable form of an IRI: a CURIE, or host + last path segment. */
export function shortIri(iri: string): string {
  const known = curie(iri);
  if (known) return known;
  try {
    const url = new URL(iri);
    const segments = url.pathname.split("/").filter(Boolean);
    const last = url.hash ? url.hash.slice(1) : segments.at(-1);
    return last ? `${url.host}/…/${decodeURIComponent(last)}` : url.host;
  } catch {
    return iri;
  }
}

export interface Cell {
  term: Term | undefined;
  text: string;
  sortKey: string | number;
}

export function cellOf(term: Term | undefined): Cell {
  if (!term) return { term, text: "", sortKey: "" };
  if (term.type === "literal") {
    const numeric = term.datatype && NUMERIC.has(term.datatype) ? Number(term.value) : NaN;
    return {
      term,
      text: term.value,
      sortKey: Number.isNaN(numeric) ? term.value.toLowerCase() : numeric,
    };
  }
  if (term.type === "uri") return { term, text: term.value, sortKey: term.value.toLowerCase() };
  if (term.type === "bnode") return { term, text: `_:${term.value}`, sortKey: term.value };
  return { term, text: JSON.stringify(term.value), sortKey: "" };
}

export interface Table {
  columns: string[];
  rows: Cell[][];
}

export function toTable(results: SelectResults): Table {
  const columns = results.head.vars;
  return {
    columns,
    rows: results.results.bindings.map((binding) => columns.map((c) => cellOf(binding[c]))),
  };
}

export function isNumericColumn(table: Table, index: number): boolean {
  let seen = 0;
  for (const row of table.rows) {
    const term = row[index]?.term;
    if (!term) continue;
    if (term.type !== "literal" || !term.datatype || !NUMERIC.has(term.datatype)) return false;
    seen += 1;
  }
  return seen > 0;
}

export interface ChartShape {
  label: number;
  value: number;
}

/** A chartable result: one category column and one numeric column, at most 50 rows. */
export function chartShape(
  table: Table,
  hint?: { label?: string | null; value?: string | null },
): ChartShape | null {
  if (table.rows.length < 2 || table.rows.length > 50) return null;
  const numeric = table.columns.map((_, i) => isNumericColumn(table, i));
  const hinted = hint?.value ? table.columns.indexOf(hint.value) : -1;
  const value = hinted >= 0 && numeric[hinted] ? hinted : numeric.lastIndexOf(true);
  if (value < 0) return null;
  const hintedLabel = hint?.label ? table.columns.indexOf(hint.label) : -1;
  const label = hintedLabel >= 0 ? hintedLabel : numeric.findIndex((n, i) => !n && i !== value);
  return label >= 0 ? { label, value } : null;
}

function csvCell(text: string): string {
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(table: Table): string {
  const lines = [table.columns.join(",")];
  for (const row of table.rows) lines.push(row.map((c) => csvCell(c.text)).join(","));
  return `${lines.join("\r\n")}\r\n`;
}

export function toMarkdown(table: Table, max = 200): string {
  const escape = (text: string) => text.replaceAll("|", "\\|").replaceAll("\n", " ");
  const lines = [
    `| ${table.columns.map(escape).join(" | ")} |`,
    `| ${table.columns.map(() => "---").join(" | ")} |`,
    ...table.rows.slice(0, max).map((row) => `| ${row.map((c) => escape(c.text)).join(" | ")} |`),
  ];
  return `${lines.join("\n")}\n`;
}
