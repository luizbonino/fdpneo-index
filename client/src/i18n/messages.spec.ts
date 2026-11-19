// Every message must compile and render: an unescaped @ { } | breaks
// vue-i18n at runtime (fdpneo-client 0.9.2 shipped such a crash). Every
// language has every key, with the placeholders of the English message
// (TASKS 13.3).
import { describe, expect, it } from "vitest";
import { createI18n } from "vue-i18n";
import { de } from "./de";
import { en } from "./en";
import { es } from "./es";
import { fr } from "./fr";
import { pseudoText } from "./index";
import { it as italian } from "./it";
import { nl } from "./nl";
import { pt } from "./pt";

const LOCALES = { en, nl, de, fr, es, it: italian, pt } as const;
const PARAMS = {
  n: 2,
  url: "u",
  pattern: "p",
  version: "1",
  page: 1,
  pages: 2,
  when: "now",
};

function leaves(value: unknown, prefix = ""): Map<string, string> {
  if (typeof value === "string") return new Map([[prefix, value]]);
  return new Map(
    Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => [
      ...leaves(child, prefix ? `${prefix}.${key}` : key),
    ]),
  );
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\s*([A-Za-z_]\w*)\s*\}/g)].map((m) => m[1]!).sort();
}

const english = leaves(en);

describe.each(Object.entries(LOCALES))("%s messages", (locale, messages) => {
  const found = leaves(messages);

  it("have exactly the English keys", () => {
    expect([...found.keys()].sort()).toEqual([...english.keys()].sort());
  });

  it("compile and render", () => {
    const i18n = createI18n({ legacy: false, locale, messages: { [locale]: messages } });
    const failures: string[] = [];
    for (const key of found.keys()) {
      try {
        const text = i18n.global.t(key, PARAMS);
        if (!text || text === key) failures.push(key);
      } catch (err) {
        failures.push(`${key}: ${String(err)}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("keep the English placeholders", () => {
    const wrong = [...english].filter(([key, text]) => {
      const translated = found.get(key);
      return (
        translated !== undefined && placeholders(translated).join() !== placeholders(text).join()
      );
    });
    expect(wrong.map(([key]) => key)).toEqual([]);
  });

  it("keep the English plural forms", () => {
    const wrong = [...english].filter(([key, text]) => {
      const translated = found.get(key);
      return (
        translated !== undefined && translated.split(" | ").length !== text.split(" | ").length
      );
    });
    expect(wrong.map(([key]) => key)).toEqual([]);
  });
});

describe("pseudo-locale", () => {
  it("keeps placeholders and plural forms", () => {
    const text = pseudoText("{n} items | one item");
    expect(text.split(" | ")).toHaveLength(2);
    expect(text).toContain("{n}");
    expect(text).toContain("ïtëms");
    expect(text.length).toBeGreaterThan("{n} items | one item".length);
  });
});
