// Every message must compile and render: an unescaped @ { } | breaks
// vue-i18n at runtime (fdpneo-client 0.9.2 shipped such a crash).
import { describe, expect, it } from "vitest";
import { createI18n } from "vue-i18n";
import { en } from "./en";

function leaves(value: unknown, prefix = ""): string[] {
  if (typeof value === "string") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    leaves(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("English messages", () => {
  it("compile and render", () => {
    const i18n = createI18n({ legacy: false, locale: "en", messages: { en } });
    const failures: string[] = [];
    for (const key of leaves(en)) {
      try {
        const text = i18n.global.t(key, {
          n: 2,
          url: "u",
          pattern: "p",
          version: "1",
          page: 1,
          pages: 2,
          when: "now",
        });
        if (!text || text === key) failures.push(key);
      } catch (err) {
        failures.push(`${key}: ${String(err)}`);
      }
    }
    expect(failures).toEqual([]);
  });
});
