import { describe, expect, it } from "vitest";
import {
  PERIOD_BOUND,
  addFacet,
  apiParams,
  chips,
  clearAll,
  fromQuery,
  isFiltered,
  removeChip,
  removeFacet,
  setNumber,
  parseBox,
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
      numbers: {},
      active: true,
      collapse: false,
      bbox: "",
      sort: "modified",
      page: 3,
      added: [],
    });
    expect(fromQuery(toQuery(state) as never, FACETS)).toEqual(state);
  });

  it("keeps temporal coverage as typed, as temporalFrom/temporalTo", () => {
    const state = fromQuery({ temporalFrom: "2010", temporalTo: "2012-06" }, FACETS);
    expect(state.dates).toEqual({ temporal: { from: "2010", to: "2012-06" } });
    const params = apiParams(state);
    expect([params.get("temporalFrom"), params.get("temporalTo")]).toEqual(["2010", "2012-06"]);
    expect(chips(state)).toEqual([{ name: "temporal", value: "2010..2012-06" }]);
    expect(PERIOD_BOUND.test("2010-05-31")).toBe(true);
    expect(PERIOD_BOUND.test("2010-13")).toBe(false);
    expect(PERIOD_BOUND.test("10")).toBe(false);
  });

  it("keeps number bounds of facets as <name>Min/<name>Max", () => {
    const state = fromQuery({ recordsMin: "100", recordsMax: "lots", otherMin: "1" }, ["records"]);
    expect(state.numbers).toEqual({ records: { min: "100", max: "" } });
    expect(apiParams(state).get("recordsMin")).toBe("100");
    const chip = chips(state)[0]!;
    expect(chip).toEqual({ name: "records", value: "100..", range: true });
    expect(removeChip(state, chip).numbers).toEqual({});
    expect(setNumber(state, "records", "max", "500").numbers.records).toEqual({
      min: "100",
      max: "500",
    });
    expect(setNumber(state, "records", "min", "").numbers).toEqual({});
  });

  it("keeps a valid area, as bbox", () => {
    const state = fromQuery({ bbox: "3.3,50.7,7.3,53.6" }, FACETS);
    expect(state.bbox).toBe("3.3,50.7,7.3,53.6");
    expect(apiParams(state).get("bbox")).toBe("3.3,50.7,7.3,53.6");
    expect(chips(state)).toEqual([{ name: "bbox", value: "3.3,50.7,7.3,53.6" }]);
    expect(fromQuery({ bbox: "7,50,3,53" }, FACETS).bbox).toBe("");
    expect(parseBox("200,0,201,1")).toBeNull();
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

  it("keeps added property facets and their filters in the URL", () => {
    const language = "prop:http://purl.org/dc/terms/language";
    const type = "prop:http://purl.org/dc/terms/type";
    let state = fromQuery({ facet: [language, "theme"], [type]: "Text" }, FACETS);
    expect(state.added).toEqual([language, type]);
    expect(state.filters).toEqual({ [type]: ["Text"] });
    expect(fromQuery(toQuery(state) as never, FACETS)).toEqual(state);
    expect(apiParams(state).getAll(type)).toEqual(["Text"]);
    expect(addFacet(state, language)).toBe(state);
    state = { ...state, page: 3 };
    expect(removeFacet(state, language)).toMatchObject({ added: [type], page: 3 });
    expect(removeFacet(state, type)).toMatchObject({ added: [language], filters: {}, page: 1 });
    // A property facet the index offers by default is not an added one.
    expect(fromQuery({ facet: language }, [...FACETS, language]).added).toEqual([]);
  });
});
