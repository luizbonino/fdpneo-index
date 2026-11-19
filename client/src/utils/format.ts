/**
 * Presentation helpers: entry states, relative times, and durations.
 *
 * The API writes durations the way Java does (`PT168H`); people think in
 * "7 days". `toAmount` picks the largest whole unit, `toIso` writes it back.
 */
import { currentLocale } from "@/i18n";

import type { EntryState } from "@/api";

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

export const STATE_TONE: Record<EntryState, Tone> = {
  ACTIVE: "ok",
  INACTIVE: "warn",
  UNREACHABLE: "bad",
  INVALID: "bad",
  UNKNOWN: "neutral",
};

export type DurationUnit = "seconds" | "minutes" | "hours" | "days";

const UNIT_SECONDS: Record<DurationUnit, number> = {
  seconds: 1,
  minutes: 60,
  hours: 3600,
  days: 86400,
};

const ISO = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/i;

export function isoToSeconds(iso: string): number | null {
  const match = ISO.exec(iso.trim());
  if (!match || /^PT?$/i.test(iso.trim())) return null;
  const [, d, h, m, s] = match;
  return Number(d ?? 0) * 86400 + Number(h ?? 0) * 3600 + Number(m ?? 0) * 60 + Number(s ?? 0);
}

export function toAmount(iso: string): { amount: number; unit: DurationUnit } {
  const seconds = isoToSeconds(iso) ?? 0;
  for (const unit of ["days", "hours", "minutes"] as const) {
    if (seconds > 0 && seconds % UNIT_SECONDS[unit] === 0) {
      return { amount: seconds / UNIT_SECONDS[unit], unit };
    }
  }
  return { amount: seconds, unit: "seconds" };
}

/** ISO-8601 as Java's Duration.toString writes it: hours, minutes, seconds. */
export function toIso(amount: number, unit: DurationUnit): string {
  const total = Math.round(amount * UNIT_SECONDS[unit]);
  if (total <= 0) return "PT0S";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `PT${hours ? `${hours}H` : ""}${minutes ? `${minutes}M` : ""}${seconds ? `${seconds}S` : ""}`;
}

export function humanDuration(iso: string, locale = currentLocale()): string {
  const { amount, unit } = toAmount(iso);
  const format = new Intl.NumberFormat(locale, {
    style: "unit",
    unit: unit.slice(0, -1),
    unitDisplay: "long",
  });
  return format.format(amount);
}

const RELATIVE_STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 86400],
  ["month", 30 * 86400],
  ["week", 7 * 86400],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];

export function relativeTime(
  iso: string | null | undefined,
  now = Date.now(),
  locale = currentLocale(),
): string {
  if (!iso) return "—";
  const seconds = (new Date(iso).getTime() - now) / 1000;
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(0, "minute");
}

export function absoluteTime(iso: string | null | undefined, locale = currentLocale()): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

/** A country IRI (lexvo, Wikidata, …) or code, shortened for display. */
export function countryLabel(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const code =
    /iso3166\/([A-Za-z]{2})$/.exec(value)?.[1] ?? (/^[A-Za-z]{2}$/.test(value) ? value : null);
  if (code) {
    try {
      return (
        new Intl.DisplayNames([currentLocale()], { type: "region" }).of(code.toUpperCase()) ?? code
      );
    } catch {
      return code.toUpperCase();
    }
  }
  return value.replace(/^.*[/#]/, "");
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/**
 * A temporal coverage for display: whole years as years ("2010 – 2015"), other
 * bounds as dates; an open end reads "since 2018", an open start "until 2020".
 */
export function formatPeriod(
  period: { start: string | null; end: string | null } | null | undefined,
  t: (key: string, values: Record<string, string>) => string,
): string {
  if (!period || (!period.start && !period.end)) return "";
  const from = period.start?.endsWith("-01-01") ? period.start.slice(0, 4) : period.start;
  const to = period.end?.endsWith("-12-31") ? period.end.slice(0, 4) : period.end;
  if (from && to) return from === to ? from : t("search.period.range", { from, to });
  return from ? t("search.period.since", { from }) : t("search.period.until", { to: to ?? "" });
}
