<script setup lang="ts">
/**
 * The directory. Filters live in the URL (?state=&q=&sort=&page=&permit=) so
 * every view of the list can be shared and survives a reload.
 */
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ENTRY_STATES, api } from "@/api";
import { session } from "@/auth/session";
import { useCounts, useEntries, useInvalidateEntries } from "@/composables/queries";
import { toast } from "@/composables/feedback";
import StateBadge from "@/components/StateBadge.vue";
import PermitBadge from "@/components/PermitBadge.vue";
import StatusHelp from "@/components/StatusHelp.vue";
import { absoluteTime, hostOf, relativeTime } from "@/utils/format";

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const invalidate = useInvalidateEntries();

const SORTS = {
  recent: "lastRetrievalTime,desc",
  registered: "registrationTime,desc",
  name: "clientUrl,asc",
} as const;
type SortKey = keyof typeof SORTS;
const PAGE_SIZE = 25;

function param(name: string): string {
  const value = route.query[name];
  return typeof value === "string" ? value : "";
}

const state = computed(() => param("state").toUpperCase());
const sortKey = computed<SortKey>(() =>
  (param("sort") as SortKey) in SORTS ? (param("sort") as SortKey) : "recent",
);
const page = computed(() => Math.max(0, Number(param("page") || "1") - 1));
const permit = computed(() => (session.isAdmin ? param("permit") || "accepted" : "accepted"));
const search = ref(param("q"));

const query = computed(() => ({
  state: state.value,
  q: param("q"),
  sort: SORTS[sortKey.value],
  page: page.value,
  size: PAGE_SIZE,
  permit: permit.value,
}));

const entries = useEntries(query);
const counts = useCounts(permit);
const pendingCounts = useCounts("pending");
const pending = computed(() =>
  session.isAdmin ? (pendingCounts.data.value?.entriesCount.ALL ?? 0) : 0,
);

function update(changes: Record<string, string | undefined>) {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...route.query, ...changes })) {
    if (typeof value === "string" && value) next[key] = value;
  }
  if (!("page" in changes)) delete next.page;
  void router.replace({ query: next });
}

let timer: ReturnType<typeof setTimeout> | undefined;
watch(search, (value) => {
  clearTimeout(timer);
  timer = setTimeout(() => update({ q: value.trim() || undefined }), 250);
});
watch(
  () => route.query.q,
  (value) => {
    if (typeof value !== "string" && search.value) search.value = "";
  },
);

// "Not seen recently" and "Not checked yet" are rare once liveness checks run;
// their tiles appear only when some entry is in that state (or it is selected).
const OCCASIONAL = new Set<string>(["INACTIVE", "UNKNOWN"]);
const tiles = computed(() => [
  { key: "", label: t("state.ALL"), count: counts.data.value?.entriesCount.ALL },
  ...ENTRY_STATES.map((s) => ({
    key: s,
    label: t(`state.${s}`),
    count: counts.data.value?.entriesCount[s],
  })).filter((tile) => !OCCASIONAL.has(tile.key) || !!tile.count || state.value === tile.key),
]);

const filtered = computed(() => !!(state.value || param("q")));
const totalPages = computed(() => entries.data.value?.totalPages ?? 0);

const checkingAll = ref(false);
async function checkAll() {
  checkingAll.value = true;
  try {
    await api.triggerAll();
    toast(t("directory.checkAllDone"));
    setTimeout(() => void invalidate(), 3000);
  } catch (err) {
    toast((err as Error).message, "error");
  } finally {
    checkingAll.value = false;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>{{ t("directory.title") }}</h1>
        <p class="lead">{{ t("directory.lead") }}</p>
      </div>
      <div v-if="session.isAdmin" class="row">
        <router-link class="btn" :to="{ name: 'register', hash: '#add' }">{{
          t("directory.addFdp")
        }}</router-link>
        <button class="btn" :disabled="checkingAll" @click="checkAll">
          {{ t("directory.checkAll") }}
        </button>
      </div>
    </header>

    <router-link v-if="pending > 0" class="notice warn pending" :to="{ name: 'approvals' }">
      {{ t("directory.pending", { n: pending }) }} →
    </router-link>

    <div class="tiles-head">
      <span class="eyebrow">{{ t("directory.byStatus") }}</span>
      <StatusHelp :permits="session.isAdmin" />
    </div>
    <nav class="tiles" :aria-label="t('directory.byStatus')">
      <button
        v-for="tile in tiles"
        :key="tile.key"
        :class="['tile', `tile-${tile.key || 'ALL'}`]"
        :aria-pressed="state === tile.key"
        @click="update({ state: tile.key || undefined })"
      >
        <span class="count">{{ tile.count ?? "–" }}</span>
        <span class="label">{{ tile.label }}</span>
      </button>
    </nav>

    <div class="toolbar">
      <label class="search">
        <span class="visually-hidden">{{ t("directory.search") }}</span>
        <input v-model="search" class="input" type="search" :placeholder="t('directory.search')" />
      </label>
      <label v-if="session.isAdmin" class="row">
        <span class="muted small">{{ t("directory.permitFilter") }}</span>
        <select
          class="input select"
          :value="permit"
          @change="update({ permit: ($event.target as HTMLSelectElement).value })"
        >
          <option value="accepted">{{ t("permit.ACCEPTED") }}</option>
          <option value="pending">{{ t("permit.PENDING") }}</option>
          <option value="rejected">{{ t("permit.REJECTED") }}</option>
          <option value="all">{{ t("state.ALL") }}</option>
        </select>
      </label>
      <label class="row">
        <span class="muted small">{{ t("directory.sort") }}</span>
        <select
          class="input select"
          :value="sortKey"
          @change="update({ sort: ($event.target as HTMLSelectElement).value })"
        >
          <option v-for="(_, key) in SORTS" :key="key" :value="key">
            {{ t(`directory.sorts.${key}`) }}
          </option>
        </select>
      </label>
    </div>

    <div v-if="entries.isError.value" class="notice error">
      {{ (entries.error.value as Error)?.message || t("common.error") }}
      <button class="btn sm" @click="entries.refetch()">{{ t("common.retry") }}</button>
    </div>

    <section
      v-else-if="entries.data.value && entries.data.value.content.length === 0"
      class="card empty"
    >
      <h2>{{ filtered ? t("directory.emptyFiltered") : t("directory.empty") }}</h2>
      <p class="muted">{{ filtered ? "" : t("directory.emptyHint") }}</p>
      <div class="row">
        <button v-if="filtered" class="btn" @click="update({ state: undefined, q: undefined })">
          {{ t("directory.clearFilters") }}
        </button>
        <router-link v-else class="btn primary" :to="{ name: 'register' }">{{
          t("directory.howTo")
        }}</router-link>
      </div>
    </section>

    <section v-else class="card flush" :aria-busy="entries.isFetching.value">
      <p class="muted small results">
        {{ t("directory.results", entries.data.value?.totalElements ?? 0) }}
      </p>
      <table class="table entries">
        <thead>
          <tr>
            <th scope="col">{{ t("directory.columns.fdp") }}</th>
            <th scope="col">{{ t("directory.columns.state") }}</th>
            <th scope="col">{{ t("directory.columns.lastSeen") }}</th>
            <th scope="col">{{ t("directory.columns.registered") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="entries.isPending.value">
            <td colspan="4" class="muted">{{ t("common.loading") }}</td>
          </tr>
          <tr v-for="entry in entries.data.value?.content ?? []" :key="entry.uuid">
            <td class="fdp">
              <router-link class="name" :to="{ name: 'entry', params: { uuid: entry.uuid } }">
                {{ entry.title || hostOf(entry.clientUrl) }}
              </router-link>
              <span class="mono small muted truncate">{{ entry.clientUrl }}</span>
            </td>
            <td>
              <div class="row">
                <StateBadge :state="entry.state" />
                <PermitBadge
                  v-if="session.isAdmin && entry.permit !== 'ACCEPTED'"
                  :permit="entry.permit"
                />
              </div>
            </td>
            <td
              :data-label="t('directory.columns.lastSeen')"
              :title="absoluteTime(entry.lastRetrievalTime)"
            >
              {{ relativeTime(entry.lastRetrievalTime) }}
            </td>
            <td
              :data-label="t('directory.columns.registered')"
              :title="absoluteTime(entry.registrationTime)"
            >
              {{ relativeTime(entry.registrationTime) }}
            </td>
          </tr>
        </tbody>
      </table>
      <nav v-if="totalPages > 1" class="pager row">
        <button class="btn sm" :disabled="page === 0" @click="update({ page: String(page) })">
          {{ t("directory.previous") }}
        </button>
        <span class="muted small">{{
          t("directory.page", { page: page + 1, pages: totalPages })
        }}</span>
        <button
          class="btn sm"
          :disabled="page + 1 >= totalPages"
          @click="update({ page: String(page + 2) })"
        >
          {{ t("directory.next") }}
        </button>
      </nav>
    </section>
  </div>
</template>

<style scoped>
.pending {
  display: block;
  font-weight: var(--fair-weight-semibold);
}
.tiles-head {
  display: flex;
  align-items: center;
  gap: var(--fair-space-1);
  margin-bottom: calc(-1 * var(--fair-space-2));
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: var(--fair-space-3);
}
.tile {
  display: grid;
  gap: 4px;
  text-align: left;
  padding: 14px 16px;
  border-radius: var(--fair-radius-lg);
  border: 1px solid var(--fair-border);
  background: var(--fair-surface);
  color: var(--fair-text);
  border-left: 4px solid var(--fair-border);
}
.tile:hover {
  background: var(--fair-code-bg);
}
.tile[aria-pressed="true"] {
  border-color: var(--tool-accent);
  box-shadow: var(--fair-focus-ring-accent);
}
.tile .count {
  font-size: var(--fair-text-2xl);
  font-weight: var(--fair-weight-bold);
  color: var(--fair-text-strong);
}
.tile .label {
  font-size: var(--fair-text-sm);
  color: var(--fair-text-muted);
}
.tile-ACTIVE {
  border-left-color: var(--fair-success);
}
.tile-INACTIVE {
  border-left-color: var(--fair-warning);
}
.tile-UNREACHABLE,
.tile-INVALID {
  border-left-color: var(--fair-danger);
}
.tile-ALL {
  border-left-color: var(--tool-accent);
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fair-space-3) var(--fair-space-6);
  align-items: center;
}
.search {
  flex: 1 1 280px;
}
.select {
  width: auto;
}
.results {
  padding: 12px 16px 0;
}
.entries .fdp {
  display: grid;
  gap: 2px;
  max-width: 520px;
}
.entries .name {
  font-weight: var(--fair-weight-semibold);
  color: var(--fair-text-strong);
}
.pager {
  justify-content: center;
  padding: 12px;
  border-top: 1px solid var(--fair-separator);
}
.empty {
  display: grid;
  gap: var(--fair-space-3);
  justify-items: start;
}
@media (max-width: 720px) {
  .entries thead {
    display: none;
  }
  .entries tr {
    display: grid;
    gap: 6px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--fair-separator);
  }
  .entries td {
    padding: 0;
    border: 0;
  }
  .entries td[data-label]::before {
    content: attr(data-label) ": ";
    color: var(--fair-text-muted);
    font-size: var(--fair-text-sm);
  }
}
</style>
