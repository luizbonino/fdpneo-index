import { describe, expect, it } from "vitest";
import {
  apiParams,
  chips,
  clearAll,
  fromQuery,
  isFiltered,
  removeChip,
  toQuery,
  toggle,
  type SearchState,
} from "./state";

const FACETS = ["theme", "keyword", "publisher"];
const HEAL = "http://publications.europa.eu/resource/authority/data-theme/HEAL";

describe("search URL state", () => {
  it("round-trips through the route query", () => {
    const state = fromQuery(
      {
        q: "cancer",
        type: "dataset",
        theme: [HEAL, "x"],
        keyword: "lungs",
        fdp: "u1",
        modifiedFrom: "2024-01-01",
        fdpState: "ACTIVE",
        sort: "modified",
        page: "3",
        unknown: "dropped",
      },
      FACETS,
    );
    expect(state).toEqual<SearchState>({
      q: "cancer",
      type: "dataset",
      filters: { theme: [HEAL, "x"], keyword: ["lungs"] },
      fdp: ["u1"],
      dates: { modified: { from: "2024-01-01", to: "" } },
      active: true,
      sort: "modified",
      page: 3,
    });
    expect(fromQuery(toQuery(state) as never, FACETS)).toEqual(state);
  });

  it("falls back on bad values", () => {
    const state = fromQuery({ type: "planet", sort: "random", page: "-2" }, FACETS);
    expect([state.type, state.sort, state.page]).toEqual(["", "", 1]);
    expect(fromQuery({ type: "distribution" }, FACETS).type).toBe("distribution");
  });

  it("builds API parameters with 0-based pages", () => {
    const state = fromQuery({ q: "x", theme: HEAL, page: "2", sort: "title" }, FACETS);
    const params = apiParams(state, { facets: ["theme"], lang: "nl" });
    expect(params.get("page")).toBe("1");
    expect(params.get("sort")).toBe("title,asc");
    expect(params.getAll("theme")).toEqual([HEAL]);
    expect(params.get("facets")).toBe("theme");
    expect(params.get("lang")).toBe("nl");
    expect(apiParams(state, { facets: [] }).get("facets")).toBe("none");
  });

  it("toggles values, makes chips and clears", () => {
    let state = fromQuery({ page: "4" }, FACETS);
    state = toggle(state, "theme", HEAL);
    expect(state.filters.theme).toEqual([HEAL]);
    expect(state.page).toBe(1);
    state = toggle(state, "fdp", "u1");
    state = { ...state, dates: { issued: { from: "2020", to: "" } }, active: true };
    expect(chips(state).map((c) => c.name)).toEqual(["theme", "fdp", "issued", "fdpState"]);
    expect(isFiltered(state)).toBe(true);
    state = removeChip(state, { name: "issued", value: "2020.." });
    expect(state.dates).toEqual({});
    state = toggle(state, "theme", HEAL);
    expect(state.filters).toEqual({});
    expect(isFiltered(clearAll(state))).toBe(false);
  });
});
