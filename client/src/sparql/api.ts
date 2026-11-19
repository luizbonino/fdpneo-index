/**
 * The SPARQL API (`/index/sparql*`): running queries, validation, schema,
 * suggestions and the query library. Results are fetched raw so the page can
 * show the exact response, offer it for download, and parse JSON results.
 */

import { i18n } from "@/i18n";
import type { components } from "@/api/schema";
import { ApiError, apiUrl, request } from "@/api/client";
import { session } from "@/auth/session";

type Schemas = components["schemas"];
export type Library = Schemas["Library"];
export type Example = Schemas["Example"];
export type Parameter = Schemas["Parameter"];

export interface SchemaTerm {
  iri: string;
  curie: string;
  label: string;
  count: number;
  /** The search fields this property feeds (TASKS 9.3): named as on the search page. */
  fields?: { name: string; label: string; help: string | null }[];
}
export interface SchemaGraph {
  iri: string;
  title: string;
  state: string | null;
}
export interface SparqlSchema {
  prefixes: Record<string, string>;
  classes: SchemaTerm[];
  properties: SchemaTerm[];
  graphs: SchemaGraph[];
}
export interface Suggestion {
  value: string;
  kind: "uri" | "literal" | "bnode";
  label: string;
  count: number;
}
export interface Validation {
  ok: boolean;
  message?: string;
  line?: number | null;
  column?: number | null;
  form?: string;
  hasLimit?: boolean;
}

export type Term =
  | { type: "uri"; value: string }
  | { type: "bnode"; value: string }
  | { type: "literal"; value: string; "xml:lang"?: string; datatype?: string }
  | { type: "triple"; value: unknown };

export interface SelectResults {
  head: { vars: string[] };
  results: { bindings: Record<string, Term>[] };
}
export interface AskResults {
  head: Record<string, never>;
  boolean: boolean;
}

export interface QueryOutcome {
  kind: "select" | "ask" | "graph";
  select?: SelectResults;
  ask?: boolean;
  text: string;
  mediaType: string;
  rows: number;
  seconds: number;
  truncated: boolean;
}

export const RESULT_ACCEPT =
  "application/sparql-results+json, text/turtle;q=0.9, application/n-triples;q=0.8";

export async function runQuery(query: string, accept = RESULT_ACCEPT): Promise<QueryOutcome> {
  const headers: Record<string, string> = {
    Accept: accept,
    "Content-Type": "application/sparql-query",
  };
  const token = session.token;
  if (token) headers.Authorization = `Bearer ${token}`;
  let response: Response;
  try {
    response = await fetch(apiUrl("/index/sparql"), { method: "POST", headers, body: query });
  } catch {
    throw new ApiError(0, i18n.global.t("common.unreachable"));
  }
  const text = await response.text();
  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`;
    let fields: Record<string, string> = {};
    try {
      const body = JSON.parse(text) as { message?: string; errors?: Record<string, unknown> };
      message = body.message ?? message;
      fields = Object.fromEntries(
        Object.entries(body.errors ?? {}).map(([k, v]) => [
          k,
          typeof v === "string" ? v : JSON.stringify(v ?? ""),
        ]),
      );
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message, fields);
  }
  const mediaType = (response.headers.get("content-type") ?? "").split(";")[0]?.trim() ?? "";
  const outcome: QueryOutcome = {
    kind: "graph",
    text,
    mediaType,
    rows: Number(response.headers.get("x-fdpindex-rows") ?? 0),
    seconds: Number(response.headers.get("x-fdpindex-seconds") ?? 0),
    truncated: response.headers.has("x-fdpindex-truncated"),
  };
  if (mediaType === "application/sparql-results+json") {
    const data = JSON.parse(text) as SelectResults | AskResults;
    if ("boolean" in data) {
      outcome.kind = "ask";
      outcome.ask = data.boolean;
    } else {
      outcome.kind = "select";
      outcome.select = data;
    }
  }
  return outcome;
}

export const sparqlApi = {
  validate: (query: string) =>
    request<Validation>("/index/sparql/validate", { method: "POST", body: { query } }),
  schema: () => request<SparqlSchema>("/index/sparql/schema"),
  examples: () => request<Library>("/index/sparql/examples"),
  expand: (id: string, parameters: Record<string, string>) =>
    request<{ query: string }>(`/index/sparql/examples/${encodeURIComponent(id)}/expand`, {
      method: "POST",
      body: { parameters },
    }),
  labels: (iris: string[]) =>
    request<{ labels: Record<string, string> }>("/index/sparql/labels", {
      method: "POST",
      body: { iris: iris.slice(0, 500) },
    }),
  suggest: (options: { predicate?: string; type?: string; q: string; limit?: number }) => {
    const params = new URLSearchParams({ q: options.q, limit: String(options.limit ?? 20) });
    if (options.predicate) params.set("predicate", options.predicate);
    if (options.type) params.set("type", options.type);
    return request<Suggestion[]>(`/index/sparql/suggest?${params.toString()}`);
  },
};
