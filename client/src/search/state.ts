/**
 * The search page's state lives in the URL: every parameter of
 * `GET /index/search` is a query parameter of `/search`, so a search can be
 * shared, bookmarked and restored with back and forward.
 *
 * Route query (`?q=&type=&theme=…&page=`) ⇄ `SearchState` ⇄ API parameters.
 * Pages are 1-based in the URL and 0-based in the API. Filters a reader added
 * on any property are `facet=prop:<IRI>` (repeatable), their values
 * `prop:<IRI>=value` like any other filter.
 */

import type { LocationQuery, LocationQueryRaw } from "vue-router";
import { KINDS, isPropertyFacet, type Kind } from "./api";

/** The tabs under the search box; other kinds (distribution, series) are reachable by URL. */
export const TYPE_TABS = ["", "dataset", "catalog", "service", "fdp"] as const;
export type TypeTab = "" | Kind;
export const SORTS = ["", "relevance", "modified", "issued", "title"] as const;
export const PAGE_SIZE = 20;
export const DATE_FIELDS = ["issued", "modified"] as const;
/** Ranges in the URL as `<name>From`/`<name>To`: the dates, and temporal coverage (TASKS 8.3). */
export const RANGE_FIELDS = [...DATE_FIELDS, "temporal"] as const;
/** What `temporalFrom`/`temporalTo` accept: a year, a month or a day. */
export const PERIOD_BOUND = /^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$/;

export interface SearchState {
  q: string;
  type: TypeTab;
  filters: Record<string, string[]>;
  fdp: string[];
  dates: Record<string, { from: string; to: string }>;
  /** Number fields within bounds, as `<name>Min`/`<name>Max` (TASKS 8.5). */
  numbers: Record<string, { min: string; max: string }>;
  active: boolean;
  /** One hit per group of copies across FDPs (`collapse=true`, TASKS 8.8). */
  collapse: boolean;
  sort: string;
  page: number;
  /** Property facets the reader added (`prop:<IRI>`), in the order added. */
  added: string[];
}

type QueryValue = LocationQuery[string] | undefined;

function list(value: QueryValue): string[] {
  const items = Array.isArray(value) ? value : [value];
  return items.filter((v): v is string => typeof v === "string" && v !== "");
}

function one(value: QueryValue): string {
  return list(value)[0] ?? "";
}

/** Read the state from the route; unknown filter names are kept only if they are facets. */
export function fromQuery(query: LocationQuery, facets: string[]): SearchState {
  const type = one(query.type) as TypeTab;
  const filters: Record<string, string[]> = {};
  const properties = Object.keys(query).filter(isPropertyFacet);
  for (const name of new Set([...facets, ...properties])) {
    const values = list(query[name]);
    if (values.length) filters[name] = values;
  }
  // A filter on a property shows its facet, added or not.
  const added = [...new Set([...list(query.facet).filter(isPropertyFacet), ...properties])].filter(
    (name) => !facets.includes(name),
  );
  const dates: SearchState["dates"] = {};
  for (const name of RANGE_FIELDS) {
    const from = one(query[`${name}From`]);
    const to = one(query[`${name}To`]);
    if (from || to) dates[name] = { from, to };
  }
  const numbers: SearchState["numbers"] = {};
  for (const name of facets) {
    const min = one(query[`${name}Min`]);
    const max = one(query[`${name}Max`]);
    if (isNumber(min) || isNumber(max))
      numbers[name] = { min: isNumber(min) ? min : "", max: isNumber(max) ? max : "" };
  }
  const page = Number.parseInt(one(query.page) || "1", 10);
  return {
    q: one(query.q),
    type: (KINDS as string[]).includes(type) ? type : "",
    filters,
    fdp: list(query.fdp),
    dates,
    numbers,
    active: one(query.fdpState) === "ACTIVE",
    collapse: one(query.collapse) === "true",
    sort: (SORTS as readonly string[]).includes(one(query.sort)) ? one(query.sort) : "",
    page: Number.isFinite(page) && page > 0 ? page : 1,
    added,
  };
}

/** The route query for a state; empty values are left out. */
export function toQuery(state: SearchState): LocationQueryRaw {
  const query: LocationQueryRaw = {};
  if (state.q) query.q = state.q;
  if (state.type) query.type = state.type;
  for (const [name, values] of Object.entries(state.filters)) {
    if (values.length) query[name] = [...values];
  }
  if (state.fdp.length) query.fdp = [...state.fdp];
  for (const [name, range] of Object.entries(state.dates)) {
    if (range.from) query[`${name}From`] = range.from;
    if (range.to) query[`${name}To`] = range.to;
  }
  for (const [name, range] of Object.entries(state.numbers)) {
    if (range.min) query[`${name}Min`] = range.min;
    if (range.max) query[`${name}Max`] = range.max;
  }
  if (state.active) query.fdpState = "ACTIVE";
  if (state.collapse) query.collapse = "true";
  if (state.sort) query.sort = state.sort;
  if (state.page > 1) query.page = String(state.page);
  if (state.added.length) query.facet = [...state.added];
  return query;
}

/** The API parameters for a state. */
export function apiParams(
  state: SearchState,
  options: {
    facets?: string[];
    size?: number;
    lang?: string | undefined;
    highlight?: boolean;
  } = {},
): URLSearchParams {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.type) params.set("type", state.type);
  for (const [name, values] of Object.entries(state.filters)) {
    for (const value of values) params.append(name, value);
  }
  for (const uuid of state.fdp) params.append("fdp", uuid);
  for (const [name, range] of Object.entries(state.dates)) {
    if (range.from) params.set(`${name}From`, range.from);
    if (range.to) params.set(`${name}To`, range.to);
  }
  for (const [name, range] of Object.entries(state.numbers)) {
    if (range.min) params.set(`${name}Min`, range.min);
    if (range.max) params.set(`${name}Max`, range.max);
  }
  if (state.active) params.set("fdpState", "ACTIVE");
  if (state.collapse) params.set("collapse", "true");
  if (state.sort) params.set("sort", sortParam(state.sort));
  params.set("page", String(state.page - 1));
  params.set("size", String(options.size ?? PAGE_SIZE));
  if (options.facets)
    params.set("facets", options.facets.length ? options.facets.join(",") : "none");
  if (options.lang) params.set("lang", options.lang);
  if (options.highlight && state.q) params.set("highlight", "true");
  return params;
}

function sortParam(sort: string): string {
  return sort === "title" ? "title,asc" : sort === "relevance" ? "relevance" : `${sort},desc`;
}

/** Add a facet on a property; adding one already there changes nothing. */
export function addFacet(state: SearchState, name: string): SearchState {
  return state.added.includes(name) ? state : { ...state, added: [...state.added, name] };
}

/** Remove an added facet and its filter. */
export function removeFacet(state: SearchState, name: string): SearchState {
  const filters = { ...state.filters };
  const filtered = name in filters;
  delete filters[name];
  return {
    ...state,
    filters,
    added: state.added.filter((n) => n !== name),
    page: filtered ? 1 : state.page,
  };
}

/** Add or remove one value of a facet; the result starts on page 1. */
export function toggle(state: SearchState, name: string, value: string): SearchState {
  if (name === "fdp") {
    const fdp = state.fdp.includes(value)
      ? state.fdp.filter((v) => v !== value)
      : [...state.fdp, value];
    return { ...state, fdp, page: 1 };
  }
  const current = state.filters[name] ?? [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  const filters = { ...state.filters, [name]: next };
  if (!next.length) delete filters[name];
  return { ...state, filters, page: 1 };
}

export function clearFacet(state: SearchState, name: string): SearchState {
  if (name === "fdp") return { ...state, fdp: [], page: 1 };
  const filters = { ...state.filters };
  delete filters[name];
  const numbers = { ...state.numbers };
  delete numbers[name];
  return { ...state, filters, numbers, page: 1 };
}

/** Set one bound of a number field ("" clears it); the result starts on page 1. */
export function setNumber(
  state: SearchState,
  name: string,
  end: "min" | "max",
  value: string,
): SearchState {
  const range = { ...(state.numbers[name] ?? { min: "", max: "" }), [end]: value.trim() };
  const numbers = { ...state.numbers, [name]: range };
  if (!range.min && !range.max) delete numbers[name];
  return { ...state, numbers, page: 1 };
}

export function isNumber(text: string): boolean {
  return text.trim() !== "" && Number.isFinite(Number(text));
}

export function clearAll(state: SearchState): SearchState {
  return { ...state, filters: {}, fdp: [], dates: {}, numbers: {}, active: false, page: 1 };
}

export interface Chip {
  name: string;
  value: string;
  /** A number range (`min..max`) rather than one value of the facet. */
  range?: boolean;
}

/** Every active filter, for the removable chips above the results. */
export function chips(state: SearchState): Chip[] {
  const out: Chip[] = [];
  for (const [name, values] of Object.entries(state.filters)) {
    for (const value of values) out.push({ name, value });
  }
  for (const value of state.fdp) out.push({ name: "fdp", value });
  for (const [name, range] of Object.entries(state.dates)) {
    if (range.from || range.to) out.push({ name, value: `${range.from}..${range.to}` });
  }
  for (const [name, range] of Object.entries(state.numbers)) {
    if (range.min || range.max)
      out.push({ name, value: `${range.min}..${range.max}`, range: true });
  }
  if (state.active) out.push({ name: "fdpState", value: "ACTIVE" });
  return out;
}

export function removeChip(state: SearchState, chip: Chip): SearchState {
  if (chip.name === "fdpState") return { ...state, active: false, page: 1 };
  if (chip.name in state.dates) {
    const dates = { ...state.dates };
    delete dates[chip.name];
    return { ...state, dates, page: 1 };
  }
  if (chip.range) {
    const numbers = { ...state.numbers };
    delete numbers[chip.name];
    return { ...state, numbers, page: 1 };
  }
  return toggle(state, chip.name, chip.value);
}

/** Whether anything narrows the search (the landing strip shows otherwise). */
export function isFiltered(state: SearchState): boolean {
  return !!(state.q || state.type || chips(state).length);
}
