/**
 * Conformance (TASKS 11.3): SHACL packs, settings, and reports per FDP and
 * per record. Types come from the server's OpenAPI document.
 */

import type { components } from "@/api/schema";
import { request } from "@/api/client";

type Schemas = components["schemas"];
export type Pack = Schemas["PackView"];
export type ConformanceSettings = Schemas["ConformanceSettingsView"];
export type ConformanceSettingsUpdate = Schemas["ConformanceSettings"];
export type EntryReport = Schemas["EntryReport"];
export type PackSummary = Schemas["PackSummary"];
export type Problem = Schemas["Problem"];
export type RecordPage = Schemas["RecordPage"];
export type RecordStatus = Schemas["RecordStatus"];
export type RecordReport = Schemas["RecordReport"];
export type Finding = Schemas["FindingView"];
export type Severity = Problem["severity"];

export interface RecordQuery {
  entry?: string;
  pack?: string;
  status?: string;
  path?: string | null;
  component?: string;
  severity?: string;
  page?: number;
  size?: number;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const conformanceApi = {
  packs: () => request<Pack[]>("/index/conformance/packs"),
  settings: () => request<ConformanceSettings>("/index/conformance/settings"),
  saveSettings: (body: ConformanceSettingsUpdate) =>
    request<ConformanceSettings>("/index/conformance/settings", { method: "PUT", body }),
  resetSettings: () =>
    request<ConformanceSettings>("/index/conformance/settings", { method: "DELETE" }),
  validate: (entry?: string) =>
    request<{ scheduled: string }>("/index/conformance/validate", {
      method: "POST",
      body: entry ? { entry } : {},
    }),
  entry: (uuid: string) => request<EntryReport>(`/index/conformance/entries/${uuid}`),
  records: (q: RecordQuery) =>
    request<RecordPage>(
      `/index/conformance/records${query({
        entry: q.entry,
        pack: q.pack,
        status: q.status,
        path: q.path,
        component: q.component,
        severity: q.severity,
        page: q.page,
        size: q.size,
      })}`,
    ),
  record: (publicId: string) =>
    request<RecordReport>(`/index/conformance/records/${encodeURIComponent(publicId)}`),
};

export const SEVERITY_TONE: Record<Severity, string> = {
  violation: "bad",
  warning: "warn",
  info: "info",
};

/** "MinCountConstraintComponent" → "MinCount". */
export function componentName(component: string): string {
  return component.replace(/ConstraintComponent$/, "");
}

/** A pack's title in the reader's language, else English, else its id. */
export function packTitle(packs: Pack[] | undefined, id: string, locale = "en"): string {
  const pack = packs?.find((p) => p.id === id);
  if (!pack) return id;
  return pack.title[locale] ?? pack.title.en ?? Object.values(pack.title)[0] ?? id;
}
