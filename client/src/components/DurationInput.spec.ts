import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import DurationInput from "./DurationInput.vue";

describe("DurationInput", () => {
  it("shows ISO durations as amount and unit, and writes them back", async () => {
    const wrapper = mount(DurationInput, {
      props: {
        id: "d",
        modelValue: "PT168H",
        "onUpdate:modelValue": (v: string) => wrapper.setProps({ modelValue: v }),
      },
    });
    const amount = wrapper.find("input");
    const unit = wrapper.find("select");
    expect((amount.element as HTMLInputElement).value).toBe("7");
    expect((unit.element as HTMLSelectElement).value).toBe("days");
    await amount.setValue("14");
    expect(wrapper.props("modelValue")).toBe("PT336H");
    await unit.setValue("hours");
    expect(wrapper.props("modelValue")).toBe("PT14H");
  });
});
