import { afterEach, describe, expect, it } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import StatusHelp from "./StatusHelp.vue";

let wrapper: VueWrapper | undefined;

function panel() {
  return document.body.querySelector<HTMLElement>(".status-help");
}

afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
});

describe("StatusHelp", () => {
  it("explains every state when the button is clicked", async () => {
    wrapper = mount(StatusHelp, { attachTo: document.body });
    const button = wrapper.get("button.info");
    expect(button.attributes("aria-expanded")).toBe("false");
    expect(panel()).toBeNull();

    await button.trigger("click");
    expect(button.attributes("aria-expanded")).toBe("true");
    const text = panel()?.textContent ?? "";
    for (const label of [
      "Active",
      "Unreachable",
      "Invalid metadata",
      "Not seen recently",
      "Not checked yet",
    ]) {
      expect(text).toContain(label);
    }
    expect(text).not.toContain("Awaiting approval");
    expect(panel()?.getAttribute("role")).toBe("dialog");
  });

  it("adds the approval states for administrators", async () => {
    wrapper = mount(StatusHelp, { props: { permits: true }, attachTo: document.body });
    await wrapper.get("button.info").trigger("click");
    expect(panel()?.textContent).toContain("Awaiting approval");
    expect(panel()?.textContent).toContain("Rejected");
  });

  it("closes on Escape and returns focus to the button", async () => {
    wrapper = mount(StatusHelp, { attachTo: document.body });
    const button = wrapper.get("button.info");
    await button.trigger("click");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(button.element);
  });

  it("closes on a click outside", async () => {
    wrapper = mount(StatusHelp, { attachTo: document.body });
    await wrapper.get("button.info").trigger("click");
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(panel()).toBeNull();
  });
});
