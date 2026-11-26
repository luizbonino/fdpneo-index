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
export type Registration = Schemas["RegistrationView"];
export type Statistics = Schemas["Statistics"];
export type MetadataHealth = Schemas["MetadataHealthView"];
export type HealthReport = Schemas["HealthReportView"];
export type Check = Schemas["CheckView"];
export type CheckPolicy = Schemas["CheckPolicyView"];
export type HealthRow = Schemas["HealthRow"];
export type NetworkProfileView = Schemas["NetworkProfileView"];
export type ProfileExample = Schemas["ProfileExample"];
export type HealthRequirement = Schemas["HealthRequirementView"];
/** Harvest provenance (TASKS 15.3). */
export type HarvestRecord = Schemas["HarvestRecordView"];
export type HarvestRecordPage = Schemas["HarvestRecordPage"];
export type HarvestOutcome = HarvestRecord["outcome"];
/** A network's benchmarks on an FDP (TASKS 15.10). */
export type EntryBenchmarks = Schemas["EntryBenchmarks"];
export type EntryBenchmark = Schemas["EntryBenchmark"];
export type BenchmarkResult = Schemas["BenchmarkResultView"];
export type BenchmarkResultDetail = Schemas["BenchmarkResultDetail"];
export type BenchmarkResultPage = Schemas["BenchmarkResultPage"];
export type NetworkBenchmark = Schemas["NetworkBenchmark"];
export type BenchmarkMode = Settings["benchmarks"]["mode"];
export const BENCHMARK_MODES: BenchmarkMode[] = ["off", "onRequest", "scheduled"];
export interface BenchmarkResultQuery {
  benchmark?: string | undefined;
  condition?: string | undefined;
  met?: boolean | undefined;
  page?: number;
  size?: number;
}
/** The structural findings and failure kinds, in display order (TASKS 15.8). */
export const HEALTH_LEVELS = ["fdp", "catalog", "dataset", "distribution", "dataservice"] as const;
export const HEALTH_FINDINGS = [
  "linked-only-in-dcat",
  "not-linked-in-dcat",
  "catalog-without-datasets",
] as const;
/** DCAT's advice, which an FDP never promised: shown, not counted against it. */
export const HEALTH_NOTES = [
  "dataset-without-distributions",
  "distribution-without-access",
] as const;
export interface EntryOptions {
  harvest?: boolean;
  /** ISO-8601 duration; "" clears it. */
  harvestInterval?: string;
  /** ISO 3166-1 alpha-2, set by an administrator; "" clears it (TASKS 15.4). */
  country?: string;
  /** The FDP's own harvest budget and other sites (TASKS 15.7); null: the defaults. */
  harvestPagesPerRun?: number | null;
  harvestPagesPerWalk?: number | null;
  harvestOrigins?: string[] | null;
}

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
  "EntryExpired",
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
  stats: (days: number) => request<Statistics>(`/index/stats${query({ days })}`),
  counts: (permit = "accepted") =>
    request<{ entriesCount: Record<string, number> }>(`/index/entries/info${query({ permit })}`),
  entry: (uuid: string) => request<EntryDetail>(`/index/entries/${uuid}`),
  entryHealth: (uuid: string) => request<MetadataHealth | null>(`/index/entries/${uuid}/health`),
  healthTable: () => request<HealthRow[]>("/index/health"),
  harvest: (id: string) => request<HarvestRecord>(`/index/harvests/${id}`),
  harvestRdf: (id: string, format: "ttl" | "jsonld") =>
    apiUrl(`/index/harvests/${id}${query({ format })}`),
  entryHarvests: (uuid: string, page = 0, size = 5) =>
    request<HarvestRecordPage>(`/index/entries/${uuid}/harvests${query({ page, size })}`),
  entryBenchmarks: (uuid: string) => request<EntryBenchmarks>(`/index/entries/${uuid}/benchmarks`),
  benchmarkResults: (uuid: string, q: BenchmarkResultQuery) =>
    request<BenchmarkResultPage>(
      `/index/entries/${uuid}/benchmarks/results${query({
        benchmark: q.benchmark,
        condition: q.condition,
        met: q.met === undefined ? undefined : String(q.met),
        page: q.page,
        size: q.size,
      })}`,
    ),
  benchmarkResult: (id: string) => request<BenchmarkResultDetail>(`/index/benchmark-results/${id}`),
  runBenchmarks: (uuid: string, record?: string) =>
    request<{ queued: number }>(`/index/entries/${uuid}/benchmarks/runs`, {
      method: "POST",
      body: record ? { record } : {},
    }),
  networkProfile: () => request<NetworkProfileView>("/index/network-profile"),
  saveNetworkProfile: (source: string) =>
    request<NetworkProfileView>("/index/network-profile", { method: "PUT", body: { source } }),
  resetNetworkProfile: () =>
    request<NetworkProfileView>("/index/network-profile", { method: "DELETE" }),
  networkExamples: () => request<ProfileExample[]>("/index/network-profile/examples"),
  setPermit: (uuid: string, permit: Permit) =>
    request<EntryDetail>(`/index/entries/${uuid}`, { method: "PUT", body: { permit } }),
  setOptions: (uuid: string, options: EntryOptions) =>
    request<EntryDetail>(`/index/entries/${uuid}`, { method: "PUT", body: { options } }),
  checkPolicy: () => request<CheckPolicy>("/index/checks"),
  startCheck: (clientUrl: string) =>
    request<Check>("/index/checks", { method: "POST", body: { clientUrl } }),
  check: (id: string) => request<Check>(`/index/checks/${id}`),
  register: (clientUrl: string) =>
    request<Registration>("/index/register", { method: "POST", body: { clientUrl } }),
  createEntry: (clientUrl: string, permit?: Permit, options?: EntryOptions) =>
    request<EntryDetail>("/index/entries", {
      method: "POST",
      body: { clientUrl, permit, options },
    }),
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
