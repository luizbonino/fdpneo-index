import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import DirectoryView from "./DirectoryView.vue";

const entry = {
  uuid: "e1",
  clientUrl: "https://fdp.example.org",
  title: "Example FDP",
  state: "ACTIVE",
  permit: "ACCEPTED",
  registrationTime: "2026-11-01T00:00:00Z",
  modificationTime: "2026-11-17T00:00:00Z",
  lastRetrievalTime: "2026-11-17T00:00:00Z",
};

// Typed with a parameter so assertions can inspect the query it was called with.
const entries = vi.fn<(query: unknown) => Promise<unknown>>(() =>
  Promise.resolve({
    content: [entry],
    totalElements: 1,
    totalPages: 1,
    number: 0,
    size: 25,
    numberOfElements: 1,
    first: true,
    last: true,
    empty: false,
  }),
);

vi.mock("@/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api")>();
  return {
    ...original,
    api: {
      ...original.api,
      entries: (q: unknown) => entries(q),
      counts: () =>
        Promise.resolve({
          entriesCount: { ALL: 1, ACTIVE: 1, INACTIVE: 0, UNREACHABLE: 0, INVALID: 0, UNKNOWN: 0 },
        }),
    },
  };
});

async function setup(path = "/") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "directory", component: DirectoryView },
      { path: "/entries/:uuid", name: "entry", component: { template: "<div />" } },
      { path: "/register", name: "register", component: { template: "<div />" } },
      { path: "/admin/approvals", name: "approvals", component: { template: "<div />" } },
    ],
  });
  await router.push(path);
  const wrapper = mount(DirectoryView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient() }]] },
  });
  await flushPromises();
  return { wrapper, router };
}

describe("DirectoryView", () => {
  it("lists entries with their state and title", async () => {
    const { wrapper } = await setup();
    expect(wrapper.text()).toContain("Example FDP");
    expect(wrapper.text()).toContain("Active");
    expect(wrapper.find(".tile-ACTIVE .count").text()).toBe("1");
  });

  it("filters by state through the URL", async () => {
    const { wrapper, router } = await setup();
    await wrapper.find(".tile-UNREACHABLE").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.state).toBe("UNREACHABLE");
    expect(entries).toHaveBeenLastCalledWith(
      expect.objectContaining({ state: "UNREACHABLE", permit: "accepted" }),
    );
  });

  it("shows the rare states' tiles only when they have entries or are selected", async () => {
    const { wrapper } = await setup();
    expect(wrapper.find(".tile-UNREACHABLE").exists()).toBe(true);
    expect(wrapper.find(".tile-INACTIVE").exists()).toBe(false);
    expect(wrapper.find(".tile-UNKNOWN").exists()).toBe(false);
    const selected = await setup("/?state=INACTIVE");
    expect(selected.wrapper.find(".tile-INACTIVE").exists()).toBe(true);
  });

  it("reads the page and sort from the URL", async () => {
    await setup("/?sort=name&page=3");
    expect(entries).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "clientUrl,asc", page: 2 }),
    );
  });
});
