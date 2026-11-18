/**
 * Typed calls to the index API. Types come from the server's OpenAPI document
 * (`npm run generate-api` writes `schema.ts`).
 */

import type { components } from "./schema";
import { apiUrl, request } from "./client";

type Schemas = components["schemas"];
export type Entry = Schemas["EntryView"];
export type EntryPage = Schemas["EntryPageView"];
export type EntryDetail = Schemas["EntryDetailView"];
export type EventSummary = Schemas["EventView"];
export type EventDetail = Schemas["EventDetailView"];
export type EventPage = Schemas["EventPageView"];
export type Permit = Schemas["EntryPermit"];
export type Settings = Schemas["IndexSettingsView"];
export type SettingsUpdate = Schemas["IndexSettings"];
export type Webhook = Schemas["WebhookView"];
export type WebhookInput = Schemas["WebhookRequest"];
export type WebhookEvent = Schemas["WebhookEvent"];
export type ServiceInfo = Schemas["ServiceInfo"];

export type EntryState = "ACTIVE" | "INACTIVE" | "UNREACHABLE" | "INVALID" | "UNKNOWN";
export const ENTRY_STATES: EntryState[] = [
  "ACTIVE",
  "UNREACHABLE",
  "INVALID",
  "INACTIVE",
  "UNKNOWN",
];
export const PERMITS: Permit[] = ["PENDING", "ACCEPTED", "REJECTED"];
export const WEBHOOK_EVENTS: WebhookEvent[] = [
  "NewEntry",
  "IncomingPing",
  "EntryValid",
  "EntryInvalid",
  "EntryUnreachable",
  "AdminTrigger",
  "WebhookPing",
];

export interface EntryQuery {
  state?: string;
  permit?: string;
  q?: string;
  sort?: string;
  page?: number;
  size?: number;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  config: () => request<ServiceInfo>("/index/config"),

  entries: (q: EntryQuery) =>
    request<EntryPage>(
      `/index/entries${query({ state: q.state, permit: q.permit, q: q.q, sort: q.sort, page: q.page, size: q.size })}`,
    ),
  counts: (permit = "accepted") =>
    request<{ entriesCount: Record<string, number> }>(`/index/entries/info${query({ permit })}`),
  entry: (uuid: string) => request<EntryDetail>(`/index/entries/${uuid}`),
  setPermit: (uuid: string, permit: Permit) =>
    request<EntryDetail>(`/index/entries/${uuid}`, { method: "PUT", body: { permit } }),
  deleteEntry: (uuid: string) => request<void>(`/index/entries/${uuid}`, { method: "DELETE" }),
  events: (uuid: string, page = 0, size = 50) =>
    request<EventPage>(`/index/entries/${uuid}/events${query({ page, size })}`),
  harvestedData: (uuid: string, format = "ttl") =>
    request<string>(`/index/entries/${uuid}/data${query({ format })}`, { accept: "text/turtle" }),
  harvestedDataUrl: (uuid: string, format: string) =>
    apiUrl(`/index/entries/${uuid}/data${query({ format })}`),

  settings: () => request<Settings>("/index/settings"),
  saveSettings: (settings: SettingsUpdate) =>
    request<Settings>("/index/settings", { method: "PUT", body: settings }),
  resetSettings: () => request<Settings>("/index/settings", { method: "DELETE" }),

  trigger: (clientUrl: string) =>
    request<void>("/index/admin/trigger", { method: "POST", body: { clientUrl } }),
  triggerAll: () => request<void>("/index/admin/trigger-all", { method: "POST" }),

  webhooks: () => request<Webhook[]>("/index/webhooks"),
  createWebhook: (input: WebhookInput) =>
    request<Webhook>("/index/webhooks", { method: "POST", body: input }),
  updateWebhook: (uuid: string, input: WebhookInput) =>
    request<Webhook>(`/index/webhooks/${uuid}`, { method: "PUT", body: input }),
  deleteWebhook: (uuid: string) => request<void>(`/index/webhooks/${uuid}`, { method: "DELETE" }),
  pingWebhook: (uuid: string) =>
    request<void>(`/index/admin/ping-webhook${query({ webhook: uuid })}`, { method: "POST" }),
};
