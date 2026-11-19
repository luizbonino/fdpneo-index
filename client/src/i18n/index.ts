/**
 * Interface languages (TASKS 13.2). English is bundled; the others load on
 * first use. The reader's choice is remembered in this browser; without one,
 * the browser's preferred languages pick the first we have.
 *
 * A pseudo-locale (`?locale=pseudo`, development builds only) stretches and
 * marks every message, to find text that is not translated or does not fit.
 */
import { createI18n } from "vue-i18n";
import { en } from "./en";

export type Messages = typeof en;

export const LOCALES = ["en", "nl", "de", "fr", "es", "it", "pt"] as const;
export type Locale = (typeof LOCALES)[number];

/** Each language's name in itself, for the language menu. */
export const ENDONYMS: Record<Locale, string> = {
  en: "English",
  nl: "Nederlands",
  de: "Deutsch",
  fr: "Français",
  es: "Español",
  it: "Italiano",
  pt: "Português",
};

const STORAGE_KEY = "fdpindex.locale";

const loaders: Record<Exclude<Locale, "en">, () => Promise<Messages>> = {
  nl: () => import("./nl").then((m) => m.nl),
  de: () => import("./de").then((m) => m.de),
  fr: () => import("./fr").then((m) => m.fr),
  es: () => import("./es").then((m) => m.es),
  it: () => import("./it").then((m) => m.it),
  pt: () => import("./pt").then((m) => m.pt),
};

export const i18n = createI18n<[Messages], string, false>({
  legacy: false,
  locale: "en",
  fallbackLocale: "en",
  messages: { en },
});

function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

/** The remembered choice, else the browser's first language we have, else English. */
export function preferredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    /* storage unavailable */
  }
  for (const tag of navigator.languages ?? [navigator.language]) {
    const base = tag?.toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return "en";
}

/** The current interface language. */
export function currentLocale(): string {
  return i18n.global.locale.value;
}

export async function setLocale(locale: string, { remember = true } = {}): Promise<void> {
  if (locale === "pseudo" && import.meta.env.DEV) {
    i18n.global.setLocaleMessage("pseudo", pseudo(en) as Messages);
  } else if (!isLocale(locale)) {
    return;
  } else if (locale !== "en" && !i18n.global.availableLocales.includes(locale)) {
    i18n.global.setLocaleMessage(locale, await loaders[locale]());
  }
  i18n.global.locale.value = locale;
  document.documentElement.lang = locale === "pseudo" ? "en" : locale;
  if (remember && locale !== "pseudo") {
    try {
      localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      /* storage unavailable */
    }
  }
}

/**
 * A per-language text from the server (a facet's label, a pack's title): the
 * interface language, then English, then any.
 */
export function localized(
  record: Record<string, string> | null | undefined,
  fallback = "",
): string {
  if (!record) return fallback;
  const locale = currentLocale();
  return (
    record[locale] ??
    record[locale.split("-")[0]!] ??
    record.en ??
    Object.values(record)[0] ??
    fallback
  );
}

/**
 * A translation of a text written in one language (a saved search's title):
 * the interface language's, else the text itself.
 */
export function translated(
  translations: Record<string, string> | null | undefined,
  text: string,
): string {
  const locale = currentLocale();
  return translations?.[locale] ?? translations?.[locale.split("-")[0]!] ?? text;
}

// --- pseudo-locale --------------------------------------------------------------

const ACCENTED: Record<string, string> = {
  a: "á",
  e: "ë",
  i: "ï",
  o: "ö",
  u: "ü",
  A: "Å",
  E: "É",
  I: "Î",
  O: "Ø",
  U: "Û",
};

/** "Search" → "[Šëárçh ~~~]": accented, a third longer; placeholders and syntax kept. */
export function pseudoText(text: string): string {
  let out = "";
  let depth = 0;
  for (const ch of text) {
    if (ch === "{") depth += 1;
    if (ch === "}") depth -= 1;
    out += depth === 0 && ch !== "}" ? (ACCENTED[ch] ?? ch) : ch;
  }
  // A third longer, in short groups: long words of their own would be unrealistic.
  const pad = ("~~~~ ".repeat(Math.ceil(text.length / 15)) || "~").trim();
  return out
    .split(" | ")
    .map((part) => `[${part} ${pad}]`)
    .join(" | ");
}

function pseudo(value: unknown): unknown {
  if (typeof value === "string") return pseudoText(value);
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, pseudo(v)]),
  );
}
