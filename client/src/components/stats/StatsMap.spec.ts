import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import StatsMap from "./StatsMap.vue";

async function setup(counts: Record<string, number>, link = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div />" } },
      { path: "/search", name: "search", component: { template: "<div />" } },
    ],
  });
  await router.push("/");
  const wrapper = mount(StatsMap, {
    props: {
      counts,
      title: "Where the FDPs are",
      describe: (n: number) => `${n} FDPs`,
      ...(link ? { link: (code: string) => ({ name: "search", query: { country: code } }) } : {}),
    },
    global: { plugins: [router] },
  });
  // The borders load on demand.
  await vi.waitFor(() => expect(wrapper.find("path").exists()).toBe(true), { timeout: 5000 });
  await flushPromises();
  return { wrapper, router };
}

describe("StatsMap", () => {
  it("draws the countries with counts, named for screen readers", async () => {
    const { wrapper } = await setup({ NL: 34, DE: 5 });
    const counted = wrapper.findAll("path.counted");
    expect(counted.map((p) => p.attributes("aria-label")).sort()).toEqual([
      "Germany: 5 FDPs",
      "Netherlands: 34 FDPs",
    ]);
    expect(counted.every((p) => p.attributes("tabindex") === "0")).toBe(true);
    // One hue, darker for more: the larger count gets the larger share of the accent.
    const share = (label: string) =>
      Number(
        /(\d+)%/.exec(
          counted.find((p) => p.attributes("aria-label") === label)!.attributes("style") ?? "",
        )?.[1],
      );
    expect(share("Netherlands: 34 FDPs")).toBeGreaterThan(share("Germany: 5 FDPs"));
    expect(wrapper.findAll(".legend li").length).toBeGreaterThan(1);
  });

  it("opens on the world and offers each continent that has counts", async () => {
    const { wrapper } = await setup({ NL: 34, DE: 5, CA: 8, KE: 1 });
    const options = wrapper.findAll(".regions label").map((l) => l.text());
    expect(options).toEqual(["World", "Africa 1", "Europe 39", "North America 8"]);
    expect((wrapper.find("input[value='world']").element as HTMLInputElement).checked).toBe(true);
    // Choosing a continent draws it at another scale: the Netherlands changes shape.
    const world = wrapper
      .findAll("path.counted")
      .find((p) => p.attributes("aria-label")?.startsWith("Netherlands"))!
      .attributes("d");
    await wrapper.find("input[value='EU']").setValue(true);
    await flushPromises();
    const europe = wrapper
      .findAll("path.counted")
      .find((p) => p.attributes("aria-label")?.startsWith("Netherlands"))!
      .attributes("d");
    expect(europe).not.toEqual(world);
    // One continent: still the world first, with that continent to zoom into.
    const only = await setup({ NL: 3 });
    expect(only.wrapper.findAll(".regions label").map((l) => l.text())).toEqual([
      "World",
      "Europe 3",
    ]);
  });

  it("opens a country's link", async () => {
    const { wrapper, router } = await setup({ BE: 472 }, true);
    const belgium = wrapper.find("path.counted");
    expect(belgium.attributes("role")).toBe("link");
    await belgium.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.country).toBe("BE");
  });
});
