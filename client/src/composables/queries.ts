/**
 * Server state through TanStack Query: one place for query keys, so a
 * mutation can invalidate exactly what it changed.
 */

import { computed, type MaybeRefOrGetter, toValue } from "vue";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, type EntryQuery } from "@/api";
import { session } from "@/auth/session";

export const keys = {
  config: ["config"] as const,
  entries: (q: EntryQuery) => ["entries", q] as const,
  counts: (permit: string) => ["counts", permit] as const,
  entry: (uuid: string) => ["entry", uuid] as const,
  events: (uuid: string, page: number) => ["events", uuid, page] as const,
  data: (uuid: string) => ["data", uuid] as const,
  settings: ["settings"] as const,
  webhooks: ["webhooks"] as const,
  stats: (days: number) => ["stats", days] as const,
};

export function useServiceInfo() {
  return useQuery({ queryKey: keys.config, queryFn: api.config, staleTime: Infinity });
}

export function useEntries(query: MaybeRefOrGetter<EntryQuery>) {
  return useQuery({
    queryKey: computed(() => [...keys.entries(toValue(query)), session.isAdmin]),
    queryFn: () => api.entries(toValue(query)),
    placeholderData: keepPreviousData,
  });
}

export function useCounts(permit: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => [...keys.counts(toValue(permit)), session.isAdmin]),
    queryFn: () => api.counts(toValue(permit)),
  });
}

export function useEntry(uuid: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => [...keys.entry(toValue(uuid)), session.isAdmin]),
    queryFn: () => api.entry(toValue(uuid)),
    retry: false,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: keys.settings,
    queryFn: api.settings,
    enabled: computed(() => session.isAdmin),
  });
}

export function useWebhooks() {
  return useQuery({
    queryKey: keys.webhooks,
    queryFn: api.webhooks,
    enabled: computed(() => session.isAdmin),
  });
}

/** Everything an entry change can affect: lists, counts, and the entry itself. */
export function useInvalidateEntries() {
  const client = useQueryClient();
  return async (uuid?: string) => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["entries"] }),
      client.invalidateQueries({ queryKey: ["counts"] }),
      uuid ? client.invalidateQueries({ queryKey: ["entry", uuid] }) : Promise.resolve(),
      uuid ? client.invalidateQueries({ queryKey: ["events", uuid] }) : Promise.resolve(),
    ]);
  };
}

/** The Statistics page (TASKS 15.1): counted hourly on the server, so kept a minute. */
export function useStats(days: MaybeRefOrGetter<number>) {
  return useQuery({
    queryKey: computed(() => [...keys.stats(toValue(days)), session.isAdmin]),
    queryFn: () => api.stats(toValue(days)),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}
