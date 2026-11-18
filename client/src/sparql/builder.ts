/**
 * The query builder's model and its SPARQL generator (search design §2.2.4).
 *
 * The model is what the form edits, and it is serialised into the URL: a
 * resource type, an optional FDP, conditions on properties (directly, or on a
 * related resource through one hop such as "its distribution's media type"),
 * extra columns, and an optional "count per" that turns the query into an
 * aggregate. The generator emits plain SPARQL with PREFIX lines, so the result
 * can be edited, shared, and run anywhere.
 */

import { KNOWN_PREFIXES, curie } from "./results";

export type Operator =
  "is" | "oneOf" | "contains" | "exists" | "missing" | "before" | "after" | "greater" | "less";

export interface Condition {
  /** Optional first hop to a related resource, e.g. dcat:distribution; "^" marks an inverse hop. */
  via?: string | undefined;
  property: string;
  operator: Operator;
  /** One or more values; IRIs when `kind` is "uri". */
  values: string[];
  kind: "uri" | "literal";
}

export interface BuilderModel {
  type: string;
  fdp: string;
  conditions: Condition[];
  columns: string[];
  /** A property IRI, "fdp" to count per FDP, or "" for a plain list. */
  countBy: string;
  limit: number;
}

export const ANY_TYPE = "";
export const TITLE = "http://purl.org/dc/terms/title";

export function emptyModel(): BuilderModel {
  return {
    type: "http://www.w3.org/ns/dcat#Dataset",
    fdp: "",
    conditions: [],
    columns: [],
    countBy: "",
    limit: 100,
  };
}

const FORBIDDEN = /[\s<>"{}|^`\\]/;

export function isIri(value: string): boolean {
  return /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) && !FORBIDDEN.test(value);
}

class Writer {
  readonly used = new Set<string>();

  iri(value: string): string {
    if (!isIri(value)) throw new Error(`Not a valid IRI: ${value}`);
    const short = curie(value);
    if (short) {
      this.used.add(short.split(":")[0] ?? "");
      return short;
    }
    return `<${value}>`;
  }

  literal(value: string): string {
    return JSON.stringify(value);
  }

  prefixes(): string {
    return [...this.used]
      .sort()
      .map((p) => `PREFIX ${p}: <${KNOWN_PREFIXES[p]}>\n`)
      .join("");
  }
}

/** A readable variable name from an IRI's local name, unique within `taken`. */
export function variableFor(iri: string, taken: Set<string>): string {
  const local = iri.split(/[#/:]/).filter(Boolean).at(-1) ?? "value";
  let base = local.replace(/[^A-Za-z0-9_]/g, "_").replace(/^(\d)/, "_$1") || "value";
  base = base.charAt(0).toLowerCase() + base.slice(1);
  let name = base;
  for (let i = 2; taken.has(name); i += 1) name = `${base}${i}`;
  taken.add(name);
  return name;
}

function isComplete(condition: Condition): boolean {
  if (!condition.property) return false;
  if (condition.operator === "exists" || condition.operator === "missing") return true;
  return condition.values.some((v) => v.trim() !== "");
}

export function generate(model: BuilderModel): string {
  const w = new Writer();
  const taken = new Set(["item", "title", "fdp", "count", "t", "any"]);
  const patterns: string[] = [];
  const filters: string[] = [];

  if (model.type) patterns.push(`?item a ${w.iri(model.type)} .`);

  model.conditions.filter(isComplete).forEach((condition) => {
    const hop = condition.via?.startsWith("^")
      ? `^${w.iri(condition.via.slice(1))}`
      : condition.via
        ? w.iri(condition.via)
        : "";
    const path = hop ? `${hop}/${w.iri(condition.property)}` : w.iri(condition.property);
    const values = condition.values.map((v) => v.trim()).filter(Boolean);
    const variable = `?${variableFor(condition.property, taken)}`;
    const term = (v: string) => (condition.kind === "uri" ? w.iri(v) : w.literal(v));
    switch (condition.operator) {
      case "missing":
        filters.push(`FILTER NOT EXISTS { ?item ${path} ?any }`);
        return;
      case "exists":
        patterns.push(`?item ${path} ${variable} .`);
        return;
      case "is":
      case "oneOf":
        if (condition.kind === "uri") {
          patterns.push(`VALUES ${variable} { ${values.map(term).join(" ")} }`);
          patterns.push(`?item ${path} ${variable} .`);
        } else {
          patterns.push(`?item ${path} ${variable} .`);
          filters.push(`FILTER(STR(${variable}) IN (${values.map(term).join(", ")}))`);
        }
        return;
      case "contains":
        patterns.push(`?item ${path} ${variable} .`);
        filters.push(
          `FILTER(${values
            .map((v) => `CONTAINS(LCASE(STR(${variable})), ${w.literal(v.toLowerCase())})`)
            .join(" || ")})`,
        );
        return;
      case "before":
      case "after": {
        const op = condition.operator === "before" ? "<" : ">=";
        patterns.push(`?item ${path} ${variable} .`);
        filters.push(`FILTER(STR(${variable}) ${op} ${w.literal(values[0] ?? "")})`);
        return;
      }
      case "greater":
      case "less": {
        const number = Number(values[0]);
        if (!Number.isFinite(number)) return;
        const op = condition.operator === "greater" ? ">" : "<";
        patterns.push(`?item ${path} ${variable} .`);
        filters.push(`FILTER(xsd:decimal(${variable}) ${op} ${number})`);
        w.used.add("xsd");
        return;
      }
    }
  });

  if (patterns.length === 0) patterns.push("?item a ?type .");
  const limit = Math.max(1, Math.min(Math.trunc(model.limit) || 100, 10000));
  const fdpValues = model.fdp ? `  VALUES ?fdp { ${w.iri(model.fdp)} }\n` : "";
  const body = (inner: string[]) =>
    `${fdpValues}  GRAPH ?fdp {\n${inner.map((line) => `    ${line}`).join("\n")}\n  }\n` +
    filters.map((f) => `  ${f}\n`).join("");

  let query: string;
  if (model.countBy === "fdp") {
    query =
      `SELECT ?fdp (COUNT(DISTINCT ?item) AS ?count)\nWHERE {\n${body(patterns)}}\n` +
      `GROUP BY ?fdp\nORDER BY DESC(?count)\nLIMIT ${limit}\n`;
  } else if (model.countBy) {
    const value = `?${variableFor(model.countBy, taken)}`;
    query =
      `SELECT ${value} (COUNT(DISTINCT ?item) AS ?count)\nWHERE {\n` +
      body([...patterns, `?item ${w.iri(model.countBy)} ${value} .`]) +
      `}\nGROUP BY ${value}\nORDER BY DESC(?count)\nLIMIT ${limit}\n`;
  } else {
    const columns = model.columns.filter((c) => c && c !== TITLE);
    const names = columns.map((c) => variableFor(c, taken));
    const optional = [
      `OPTIONAL { ?item ${w.iri(TITLE)} ?t }`,
      ...columns.map((c, i) => `OPTIONAL { ?item ${w.iri(c)} ?_${names[i]} }`),
    ];
    const projections = names.map((n) => `(SAMPLE(?_${n}) AS ?${n})`).join(" ");
    query =
      `SELECT ?item (SAMPLE(?t) AS ?title)${projections ? ` ${projections}` : ""} ?fdp\nWHERE {\n` +
      body([...patterns, ...optional]) +
      `}\nGROUP BY ?item ?fdp\nORDER BY ?title\nLIMIT ${limit}\n`;
  }
  return w.prefixes() + (w.used.size ? "\n" : "") + query;
}

/** The model as a URL-safe string, for shareable builder links. */
export function encodeModel(model: BuilderModel): string {
  return btoa(unescape(encodeURIComponent(JSON.stringify(model))));
}

export function decodeModel(text: string): BuilderModel | null {
  try {
    const parsed = JSON.parse(decodeURIComponent(escape(atob(text)))) as Partial<BuilderModel>;
    return {
      ...emptyModel(),
      ...parsed,
      conditions: parsed.conditions ?? [],
      columns: parsed.columns ?? [],
    };
  } catch {
    return null;
  }
}
