/**
 * The search API (`/index/search/*`). The server returns free-form JSON for
 * these routes, so the shapes are declared here rather than generated.
 */

import { apiUrl, request } from "@/api/client";

export type Kind = "fdp" | "catalog" | "dataset" | "series" | "service" | "distribution" | "other";
export const KINDS: Kind[] = [
  "dataset",
  "series",
  "catalog",
  "service",
  "fdp",
  "distribution",
  "other",
];

export interface ValueView {
  value: string;
  label: string;
  lang?: string;
}

export interface FdpView {
  uuid: string;
  clientUrl: string;
  title: string;
  state: string;
}

/** A piece of a highlighted title or description; `match` marks a searched word. */
export interface Segment {
  text: string;
  match: boolean;
}

export interface Period {
  start: string | null;
  end: string | null;
}

export interface Hit {
  id: string;
  iri: string;
  type: Kind;
  typeIris: string[];
  title: string;
  titleLang: string | null;
  titleFallback: boolean;
  description: string | null;
  score: number | null;
  completeness: number;
  fdp: FdpView;
  catalog: { id: string; iri: string; title: string } | null;
  issued: string | null;
  modified: string | null;
  /** Temporal coverage (TASKS 8.3): first and last day, either may be open. */
  temporal: Period | null;
  landingPage: string | null;
  sourceUrl: string;
  facts: Record<string, number>;
  values: Record<string, ValueView[]>;
  /** With `highlight=true` and words: title and description with the matches marked. */
  highlight?: { title: Segment[]; description?: Segment[] };
  distributions?: { id: string; iri: string; title: string; values: Record<string, ValueView[]> }[];
}

export interface Bucket {
  value: string;
  label: string;
  count: number;
  state?: string | null;
}

export interface FacetResult {
  buckets: Bucket[];
  more: boolean;
  /** Counted on a sample of a large result set and scaled up. */
  approximate?: boolean;
}

export interface SearchResult {
  query: { q: string; types: Kind[]; sort: string; lang: string };
  totalResults: number;
  startIndex: number;
  itemsPerPage: number;
  page: number;
  took: number;
  hits: Hit[];
  facets: Record<string, FacetResult>;
  /** Present when a very broad search was ranked among its newest matches only. */
  ranking?: { candidates: number };
}

export interface FacetInfo {
  /** A profile field, `fdp`, or `prop:<IRI>` for a property facet. */
  name: string;
  label: Record<string, string>;
  help: Record<string, string>;
  group: "main" | "more";
  kind: string;
  /** Fields with a hierarchy (Subject): the filter for "this or narrower", e.g. `subject^`. */
  hierarchy?: string;
  /** Property facets: the property and its prefixed name, when it has one. */
  property?: string;
  curie?: string | null;
}

/** Facets on any property of the harvested metadata are named `prop:<IRI>`. */
export const PROPERTY_FACET = "prop:";

export function isPropertyFacet(name: string): boolean {
  return name.startsWith(PROPERTY_FACET);
}

/** A readable name for a property facet from its IRI: `…/terms/accessRights` → "Access rights". */
export function propertyLabel(name: string): string {
  const iri = name.startsWith(PROPERTY_FACET) ? name.slice(PROPERTY_FACET.length) : name;
  const local =
    iri
      .replace(/[/#]+$/, "")
      .split(/[/#:]/)
      .pop() || iri;
  const words = local
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return words ? words[0]!.toUpperCase() + words.slice(1).toLowerCase() : iri;
}

/** A concept in a facet tree, counting resources tagged with it or anything narrower. */
export interface TreeNode {
  value: string;
  label: string;
  count: number;
  children: TreeNode[];
}

/** `GET /index/search/suggest`: what the search box offers while typing. */
export type Suggestion =
  | { kind: "resource"; id: string; title: string; type: Kind }
  | { kind: "query"; text: string; count: number }
  | { kind: "value"; field: string; value: string; label: string; count: number };

/** `GET /index/search/properties`: what the "add a filter" picker offers. */
export interface PropertyInfo {
  name: string;
  property: string;
  curie: string | null;
  label: string;
  resources: number;
}

export interface PropertyList {
  visitors: boolean;
  properties: PropertyInfo[];
}

export type Placement = "main" | "more" | "hidden";

export interface FacetSetting {
  name: string;
  placement: Placement;
  label: Record<string, string>;
  help: Record<string, string>;
}

/** `GET`/`PUT /index/search/profile` (administrators): the profile and the overlay on it. */
export interface ProfileView {
  hash: string;
  profile: { fields: Record<string, { kind: string; facet?: string | null }> };
  overlay: Record<string, unknown> | null;
  indexing: boolean;
}

/** `GET`/`PUT /index/search/settings` (administrators). */
export interface SearchSettings {
  facets: FacetSetting[] | null;
  properties: { visitors: boolean; hidden: string[] };
  defaultLanguage: string;
  isDefault?: boolean;
}

export interface Landing {
  counters: Record<Kind, number>;
  landing: Kind[];
  fdps: { total: number; byState: Record<string, number> };
  facets: FacetInfo[];
  /** Whether visitors may add filters on any property. */
  properties: { visitors: boolean };
  /** Languages offered for titles and labels, and the index's default. */
  languages: string[];
  defaultLanguage: string;
  fields: Record<string, { label: Record<string, string>; kind: string }>;
  cards: Partial<Record<Kind, string[]>>;
  indexing: { pending: number };
}

/** `GET /index/search/sparql`: the search as SPARQL, with what it approximates. */
export interface SearchSparql {
  query: string;
  prefixes: Record<string, string>;
  approximation: string[];
}

export interface Brief {
  id: string;
  iri: string;
  type: Kind;
  title: string;
}

export interface ResourceDetail {
  id: string;
  iri: string;
  type: Kind;
  typeIris: string[];
  title: string;
  titleLang: string | null;
  titles: Record<string, string>;
  description: string | null;
  descriptions: Record<string, string>;
  completeness: number;
  issued: string | null;
  modified: string | null;
  temporal: Period | null;
  landingPage: string | null;
  sourceUrl: string;
  harvestedAt: string;
  facts: Record<string, number | boolean>;
  fdp: FdpView;
  parents: Brief[];
  children: Brief[];
  childCount: number;
  values: Record<string, ValueView[]>;
}

/** Fields labelled by the client's copy rather than by the profile. */
export const LOCAL_FIELDS = new Set([
  "issued",
  "modified",
  "temporal",
  "landingPage",
  "datasetCount",
  "distributionCount",
]);

export const RDF_FORMATS = [
  { key: "ttl", label: "Turtle", ext: "ttl", type: "text/turtle" },
  { key: "jsonld", label: "JSON-LD", ext: "jsonld", type: "application/ld+json" },
  { key: "nt", label: "N-Triples", ext: "nt", type: "application/n-triples" },
] as const;

export const searchApi = {
  search: (params: URLSearchParams) => request<SearchResult>(`/index/search?${params}`),
  facet: (field: string, params: URLSearchParams, contains: string) => {
    const query = new URLSearchParams(params);
    for (const key of ["page", "size", "sort", "facets", "facetSize"]) query.delete(key);
    query.set("field", field);
    if (contains) query.set("contains", contains);
    return request<FacetResult>(`/index/search/facets?${query}`);
  },
  tree: (field: string, params: URLSearchParams) => {
    const query = new URLSearchParams(params);
    for (const key of ["page", "size", "sort", "facets", "facetSize", "highlight"])
      query.delete(key);
    query.set("field", field);
    query.set("tree", "true");
    return request<{ tree: TreeNode[]; filter: string; approximate?: boolean }>(
      `/index/search/facets?${query}`,
    );
  },
  sparql: (params: URLSearchParams) => request<SearchSparql>(`/index/search/sparql?${params}`),
  landing: () => request<Landing>("/index/search/landing"),
  suggest: (q: string, types: string[], lang?: string) => {
    const query = new URLSearchParams({ q });
    for (const type of types) query.append("type", type);
    if (lang) query.set("lang", lang);
    return request<{ query: string; suggestions: Suggestion[] }>(`/index/search/suggest?${query}`);
  },
  properties: (types: string[], contains = "") => {
    const query = new URLSearchParams();
    for (const type of types) query.append("type", type);
    if (contains) query.set("contains", contains);
    return request<PropertyList>(`/index/search/properties?${query}`);
  },
  settings: () => request<SearchSettings>("/index/search/settings"),
  saveSettings: (settings: SearchSettings) =>
    request<SearchSettings>("/index/search/settings", {
      method: "PUT",
      body: {
        facets: settings.facets,
        properties: settings.properties,
        defaultLanguage: settings.defaultLanguage,
      },
    }),
  profile: () => request<ProfileView>("/index/search/profile"),
  saveProfile: (overlay: Record<string, unknown>) =>
    request<ProfileView>("/index/search/profile", { method: "PUT", body: overlay }),
  resetProfile: () => request<ProfileView>("/index/search/profile", { method: "DELETE" }),
  resetSettings: () => request<SearchSettings>("/index/search/settings", { method: "DELETE" }),
  resource: (id: string, lang?: string) =>
    request<ResourceDetail>(
      `/index/search/resources/${encodeURIComponent(id)}${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`,
    ),
  triples: (id: string, format: string) =>
    request<string>(
      `/index/search/resources/${encodeURIComponent(id)}?format=${encodeURIComponent(format)}`,
      { accept: "*/*" },
    ),
  tripleUrl: (id: string, format: string) =>
    apiUrl(
      `/index/search/resources/${encodeURIComponent(id)}?format=${encodeURIComponent(format)}`,
    ),
};
