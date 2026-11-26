/** Harvest outcomes as message keys and chip tones, and a walk's duration (TASKS 15.3). */
import { currentLocale } from "@/i18n";

export type OutcomeKey = "inProgress" | "complete" | "partial" | "failed";

export function outcomeKey(outcome: string): OutcomeKey {
  return outcome === "in progress"
    ? "inProgress"
    : outcome === "complete" || outcome === "partial" || outcome === "failed"
      ? outcome
      : "partial";
}

export function outcomeTone(outcome: string): "ok" | "warn" | "bad" | "info" {
  return (
    ({ complete: "ok", partial: "warn", failed: "bad" }[outcome] as
      "ok" | "warn" | "bad" | undefined) ?? "info"
  );
}

/** How long from one instant to another, in the largest whole unit that fits. */
export function span(from: string, to: string, locale = currentLocale()): string {
  const seconds = Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000);
  const [value, unit]: [number, Intl.NumberFormatOptions["unit"]] =
    seconds < 90
      ? [Math.round(seconds), "second"]
      : seconds < 5400
        ? [Math.round(seconds / 60), "minute"]
        : [Math.round(seconds / 3600), "hour"];
  return new Intl.NumberFormat(locale, { style: "unit", unit, unitDisplay: "long" }).format(value);
}
