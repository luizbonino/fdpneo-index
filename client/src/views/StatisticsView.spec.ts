import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import StatisticsView from "./StatisticsView.vue";

// jsdom has no canvas: the charts render as placeholders; their tables carry the values.
vi.mock("vue-chartjs", () => ({
  Bar: { template: "<canvas />" },
  Line: { template: "<canvas />" },
}));

const stats = vi.fn<(days: number) => Promise<unknown>>((days) =>
  Promise.resolve({
    generatedAt: "2026-11-24T09:00:00+00:00",
    fdps: {
      total: 259,
      byState: { ACTIVE: 58, INACTIVE: 0, UNREACHABLE: 95, INVALID: 21, UNKNOWN: 85 },
    },
    records: {
      total: 4300,
      byKind: {
        fdp: 58,
        catalog: 120,
        dataset: 2900,
        series: 0,
        service: 22,
        distribution: 1200,
        other: 0,
      },
    },
    largest: [
      {
        uuid: "e1",
        clientUrl: "https://fdp.example.org",
        title: "Example FDP",
        records: 1234,
      },
    ],
    conformance: [
      {
        pack: "dcat-ap",
        specification: "http://data.europa.eu/r5r/",
        title: { en: "DCAT-AP 3.0.1" },
        checked: 4000,
        conforms: 1000,
        byKind: {},
      },
    ],
    registrations: [
      { month: "2026-10", count: 3 },
      { month: "2026-11", count: 256 },
    ],
    published: [
      { year: 2024, count: 10 },
      { year: 2025, count: 0 },
    ],
    history: {
      days: days === 30 ? ["2026-11-23", "2026-11-24"] : ["2026-11-22", "2026-11-23", "2026-11-24"],
      series: {
        "fdps.total": days === 30 ? [250, 259] : [240, null, 259],
        "fdps.state.ACTIVE": [50, 58, 58].slice(days === 30 ? 1 : 0),
        "records.total": [4000, 4300].concat(days === 30 ? [] : [4300]),
        "records.kind.dataset": [2800, 2900].concat(days === 30 ? [] : [2900]),
      },
    },
  }),
);

vi.mock("@/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api")>();
  return {
    ...original,
    api: {
      ...original.api,
      stats: (days: number) => stats(days),
      config: () => Promise.resolve({ sparql: null, search: null }),
    },
  };
});

async function setup(path = "/statistics") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/statistics", name: "statistics", component: StatisticsView },
      { path: "/entries/:uuid", name: "entry", component: { template: "<div />" } },
      { path: "/sparql", name: "sparql", component: { template: "<div />" } },
    ],
  });
  await router.push(path);
  const wrapper = mount(StatisticsView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient() }]] },
  });
  await flushPromises();
  return { wrapper, router };
}

describe("StatisticsView", () => {
  it("shows the headline numbers and their change over the period", async () => {
    const { wrapper } = await setup();
    const tiles = wrapper.findAll(".tile").map((t) => t.text());
    expect(tiles[0]).toContain("FDPs listed");
    expect(tiles[0]).toContain("259");
    expect(tiles[0]).toContain("+19 since");
    expect(tiles[1]).toContain("58");
    expect(tiles[2]).toContain("4,300");
    expect(tiles[3]).toContain("2,900");
    expect(stats).toHaveBeenLastCalledWith(365);
  });

  it("puts every chart's values in a table", async () => {
    const { wrapper } = await setup();
    const text = wrapper.text();
    expect(text).toContain("Unreachable");
    expect(text).toContain("Datasets");
    // Records by type, largest first.
    const kinds = wrapper
      .findAll("section[aria-labelledby='h-kinds'] tbody th")
      .map((th) => th.text());
    expect(kinds.slice(0, 2)).toEqual(["Datasets", "Distributions"]);
    expect(text).toContain("25%");
    expect(text).toContain("1,000 of 4,000 records");
    expect(wrapper.find("a[href='/entries/e1']").text()).toBe("Example FDP");
  });

  it("keeps the period in the URL", async () => {
    const { wrapper, router } = await setup();
    await wrapper.find("input[value='30']").setValue(true);
    await flushPromises();
    expect(router.currentRoute.value.query.days).toBe("30");
    expect(stats).toHaveBeenLastCalledWith(30);
  });

  it("says when counts over time begin", async () => {
    stats.mockResolvedValueOnce({
      generatedAt: "2026-11-24T09:00:00+00:00",
      fdps: { total: 0, byState: {} },
      records: null,
      largest: [],
      conformance: null,
      registrations: [],
      published: [],
      history: { days: [], series: {} },
    });
    const { wrapper } = await setup();
    expect(wrapper.text()).toContain("Counts over time start with the first daily count");
    expect(wrapper.text()).toContain("Search is off");
    expect(wrapper.find("section[aria-labelledby='h-kinds']").exists()).toBe(false);
  });
});
