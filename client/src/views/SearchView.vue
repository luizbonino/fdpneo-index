<script setup lang="ts">
/**
 * Faceted search over every accepted FDP's metadata (search design §6).
 *
 * The URL is the state (`?q=&type=&theme=&…&page=`): filters, sort and page
 * are read from and written to the route, so back, forward, reload and
 * shared links all work. Facet counts are disjunctive (a facet ignores its
 * own selection), so a reader sees what a second value would add.
 */
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { toast } from "@/composables/feedback";
import { LOCAL_FIELDS, searchApi, type Kind } from "@/search/api";
import {
  DATE_FIELDS,
  SORTS,
  TYPE_TABS,
  apiParams,
  chips,
  clearAll,
  clearFacet,
  fromQuery,
  isFiltered,
  removeChip,
  toQuery,
  toggle,
  type Chip,
  type SearchState,
} from "@/search/state";
import FacetGroup from "@/components/search/FacetGroup.vue";
import FacetDialog from "@/components/search/FacetDialog.vue";
import ResourceCard from "@/components/search/ResourceCard.vue";
import HelpTip from "@/components/HelpTip.vue";
import { useServiceInfo } from "@/composables/queries";
import { encodeQuery } from "@/sparql/history";

const route = useRoute();
const router = useRouter();
const info = useServiceInfo();
const { t } = useI18n();

const landing = useQuery({
  queryKey: ["search-landing"],
  queryFn: searchApi.landing,
  staleTime: 60_000,
});
const facetInfo = computed(() => landing.data.value?.facets ?? []);
const facetNames = computed(() => facetInfo.value.map((f) => f.name));

const state = computed<SearchState>(() => fromQuery(route.query, facetNames.value));

function go(next: SearchState) {
  void router.push({ query: toQuery(next) });
}

// --- search box ------------------------------------------------------------------

const text = ref(state.value.q);
watch(
  () => state.value.q,
  (q) => {
    text.value = q;
  },
);
function submit() {
  go({
    ...state.value,
    q: text.value.trim(),
    page: 1,
    sort: text.value.trim() ? "" : state.value.sort,
  });
}

// --- facets ----------------------------------------------------------------------

const showMoreFacets = ref(false);
const mainFacets = computed(() => facetInfo.value.filter((f) => f.group === "main"));
const moreFacets = computed(() => facetInfo.value.filter((f) => f.group === "more"));
const requested = computed(() => {
  const more = moreFacets.value
    .filter((f) => showMoreFacets.value || (state.value.filters[f.name]?.length ?? 0) > 0)
    .map((f) => f.name);
  return ["type", ...mainFacets.value.map((f) => f.name), "fdp", ...more];
});

const params = computed(() => apiParams(state.value, { facets: requested.value }));
const results = useQuery({
  queryKey: computed(() => ["search", params.value.toString(), session.isAdmin]),
  queryFn: () => searchApi.search(params.value),
  placeholderData: keepPreviousData,
  enabled: computed(() => landing.isSuccess.value),
  retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
});
const data = computed(() => results.data.value);

function label(record: Record<string, string> | undefined, fallback: string): string {
  return record?.en ?? Object.values(record ?? {})[0] ?? fallback;
}
function facetTitle(name: string): string {
  if (name === "fdp") return t("search.fdpFacet");
  if (name === "type") return t("search.typeFacet");
  const info = facetInfo.value.find((f) => f.name === name);
  return label(info?.label, name);
}
function fieldLabel(name: string): string {
  if (LOCAL_FIELDS.has(name)) return t(`search.fields.${name}`);
  return label(landing.data.value?.fields[name]?.label, name);
}
function facetHelp(name: string): string | undefined {
  if (name === "fdp") return t("search.fdpFacetHelp");
  const info = facetInfo.value.find((f) => f.name === name);
  return info ? label(info.help, "") || undefined : undefined;
}
function selected(name: string): string[] {
  return name === "fdp" ? state.value.fdp : (state.value.filters[name] ?? []);
}

const dialogFacet = ref<string | null>(null);

// --- tabs, chips, sort, paging -----------------------------------------------------

const typeCounts = computed(() => {
  const buckets = data.value?.facets.type?.buckets ?? [];
  const counts: Record<string, number> = {};
  for (const bucket of buckets) counts[bucket.value] = bucket.count;
  counts[""] = buckets
    .filter((b) => b.value !== "distribution")
    .reduce((sum, b) => sum + b.count, 0);
  counts.dataset = (counts.dataset ?? 0) + (counts.series ?? 0);
  return counts;
});

const labelCache = computed(() => {
  const map = new Map<string, string>();
  for (const [name, facet] of Object.entries(data.value?.facets ?? {})) {
    for (const bucket of facet.buckets) map.set(`${name}\u0000${bucket.value}`, bucket.label);
  }
  return map;
});
function chipLabel(chip: Chip): string {
  if (chip.name === "fdpState") return t("search.onlyActive");
  if ((DATE_FIELDS as readonly string[]).includes(chip.name)) {
    const [from, to] = chip.value.split("..");
    return `${fieldLabel(chip.name)}: ${from || "…"} – ${to || "…"}`;
  }
  const value = labelCache.value.get(`${chip.name}\u0000${chip.value}`) ?? chip.value;
  return `${facetTitle(chip.name)}: ${value}`;
}

const activeChips = computed(() => chips(state.value));
const filtered = computed(() => isFiltered(state.value));
const total = computed(() => data.value?.totalResults ?? 0);
// The server pages through at most 10 000 results (fewer when a broad search was ranked
// among its newest matches only).
const MAX_WINDOW = 10_000;
const pages = computed(() => {
  const reachable = Math.min(total.value, data.value?.ranking?.candidates ?? MAX_WINDOW);
  return Math.max(1, Math.ceil(reachable / (data.value?.itemsPerPage ?? 20)));
});
const sortValue = computed(() => state.value.sort || (state.value.q ? "relevance" : "modified"));
const empty = computed(() => {
  const counters = landing.data.value?.counters;
  return !!counters && Object.values(counters).every((n) => n === 0);
});
const busy = computed(
  () => results.error.value instanceof ApiError && results.error.value.status === 503,
);
const filterCount = computed(() => activeChips.value.length);
const filtersOpen = ref(false);

function setDate(name: string, end: "from" | "to", value: string) {
  const current = state.value.dates[name] ?? { from: "", to: "" };
  const range = { ...current, [end]: value };
  const dates = { ...state.value.dates, [name]: range };
  if (!range.from && !range.to) delete dates[name];
  go({ ...state.value, dates, page: 1 });
}

const heading = ref<HTMLElement>();
function page(to: number) {
  go({ ...state.value, page: to });
  void nextTick(() => heading.value?.focus());
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href);
    toast(t("search.linkCopied"));
  } catch {
    toast(location.href);
  }
}

/** The same search as a SPARQL query, opened in the editor. */
const sparqlBusy = ref(false);
async function showSparql() {
  sparqlBusy.value = true;
  try {
    const { query } = await searchApi.sparql(params.value);
    const q = await encodeQuery(query);
    await router.push({ name: "sparql", query: { tab: "editor", q } });
  } catch (err) {
    toast(err instanceof ApiError ? err.message : t("search.sparqlFailed"));
  } finally {
    sparqlBusy.value = false;
  }
}

/** A landing counter opens that kind (a tab, or a type filter for distributions). */
function openKind(kind: Kind) {
  go({ ...state.value, type: kind, page: 1 });
}

const counters = computed(() => {
  const info = landing.data.value;
  if (!info) return [];
  return info.landing.map((kind: Kind) => ({ kind, count: info.counters[kind] ?? 0 }));
});
</script>

<template>
  <div class="page search-page">
    <header class="page-head">
      <div>
        <h1>{{ t("search.title") }}</h1>
        <p class="lead">{{ t("search.lead") }}</p>
      </div>
    </header>

    <form class="searchbar" role="search" @submit.prevent="submit">
      <label class="box">
        <span class="visually-hidden">{{ t("search.placeholder") }}</span>
        <input
          v-model="text"
          class="input"
          type="search"
          name="q"
          maxlength="512"
          :placeholder="t('search.placeholder')"
          autocomplete="off"
        />
      </label>
      <button class="btn primary" type="submit">{{ t("search.submit") }}</button>
    </form>
    <p class="muted small hint">{{ t("search.syntax") }}</p>

    <nav class="tabs" :aria-label="t('search.typeFacet')">
      <button
        v-for="tab in TYPE_TABS"
        :key="tab"
        type="button"
        class="tab"
        :aria-pressed="state.type === tab"
        @click="go({ ...state, type: tab, page: 1 })"
      >
        {{ t(`search.tabs.${tab || "all"}`) }}
        <span v-if="data" class="count">{{ (typeCounts[tab] ?? 0).toLocaleString() }}</span>
      </button>
    </nav>

    <section v-if="!filtered && landing.data.value && !empty" class="landing card">
      <ul class="counters">
        <li v-for="counter in counters" :key="counter.kind">
          <button type="button" class="counter" @click="openKind(counter.kind)">
            <span class="n">{{ counter.count.toLocaleString() }}</span>
            <span class="what">{{ t(`search.plural.${counter.kind}`) }}</span>
          </button>
        </li>
      </ul>
      <div class="explainer">
        <h2>{{ t("search.hierarchyTitle") }}</h2>
        <p>{{ t("search.hierarchy") }}</p>
      </div>
    </section>

    <p v-if="landing.data.value?.indexing.pending" class="notice warn">
      {{ t("search.indexing", landing.data.value.indexing.pending) }}
    </p>

    <section v-if="empty" class="card empty">
      <h2>{{ t("search.emptyIndex") }}</h2>
      <p class="muted">{{ t("search.emptyIndexHint") }}</p>
      <router-link class="btn" :to="{ name: 'directory' }">{{ t("common.home") }}</router-link>
    </section>

    <div v-else class="layout">
      <aside :class="['sidebar', { open: filtersOpen }]" :aria-label="t('search.filters')">
        <div class="sidebar-head">
          <h2>{{ t("search.filters") }}</h2>
          <button
            v-if="filterCount"
            type="button"
            class="btn ghost sm"
            @click="go(clearAll(state))"
          >
            {{ t("search.clearAll") }}
          </button>
          <button type="button" class="btn sm close" @click="filtersOpen = false">
            {{ t("search.showResults", total) }}
          </button>
        </div>

        <label class="check active">
          <input
            type="checkbox"
            :checked="state.active"
            @change="go({ ...state, active: !state.active, page: 1 })"
          />
          {{ t("search.onlyActive") }}
          <HelpTip :title="t('search.onlyActive')" :text="t('search.onlyActiveHelp')" />
        </label>

        <FacetGroup
          v-for="facet in mainFacets"
          :key="facet.name"
          :name="facet.name"
          :title="facetTitle(facet.name)"
          :help="facetHelp(facet.name)"
          :buckets="data?.facets[facet.name]?.buckets ?? []"
          :more="data?.facets[facet.name]?.more ?? false"
          :approximate="data?.facets[facet.name]?.approximate ?? false"
          :selected="selected(facet.name)"
          @toggle="(value) => go(toggle(state, facet.name, value))"
          @clear="go(clearFacet(state, facet.name))"
          @more="dialogFacet = facet.name"
        />
        <FacetGroup
          name="fdp"
          :title="facetTitle('fdp')"
          :help="facetHelp('fdp')"
          :buckets="data?.facets.fdp?.buckets ?? []"
          :more="data?.facets.fdp?.more ?? false"
          :approximate="data?.facets.fdp?.approximate ?? false"
          :selected="state.fdp"
          @toggle="(value) => go(toggle(state, 'fdp', value))"
          @clear="go(clearFacet(state, 'fdp'))"
          @more="dialogFacet = 'fdp'"
        />

        <fieldset class="dates">
          <legend>{{ t("search.dates") }}</legend>
          <div v-for="name in DATE_FIELDS" :key="name" class="range">
            <span class="small">{{ fieldLabel(name) }}</span>
            <label>
              <span class="visually-hidden">{{ fieldLabel(name) }} {{ t("search.from") }}</span>
              <input
                class="input"
                type="date"
                :value="state.dates[name]?.from ?? ''"
                @change="setDate(name, 'from', ($event.target as HTMLInputElement).value)"
              />
            </label>
            <span aria-hidden="true">–</span>
            <label>
              <span class="visually-hidden">{{ fieldLabel(name) }} {{ t("search.to") }}</span>
              <input
                class="input"
                type="date"
                :value="state.dates[name]?.to ?? ''"
                @change="setDate(name, 'to', ($event.target as HTMLInputElement).value)"
              />
            </label>
          </div>
        </fieldset>

        <template v-if="showMoreFacets || moreFacets.some((f) => selected(f.name).length)">
          <FacetGroup
            v-for="facet in moreFacets"
            :key="facet.name"
            :name="facet.name"
            :title="facetTitle(facet.name)"
            :help="facetHelp(facet.name)"
            :buckets="data?.facets[facet.name]?.buckets ?? []"
            :more="data?.facets[facet.name]?.more ?? false"
            :approximate="data?.facets[facet.name]?.approximate ?? false"
            :selected="selected(facet.name)"
            @toggle="(value) => go(toggle(state, facet.name, value))"
            @clear="go(clearFacet(state, facet.name))"
            @more="dialogFacet = facet.name"
          />
        </template>
        <button
          v-if="moreFacets.length"
          type="button"
          class="btn ghost sm more-facets"
          :aria-expanded="showMoreFacets"
          @click="showMoreFacets = !showMoreFacets"
        >
          {{ showMoreFacets ? t("search.fewerFacets") : t("search.moreFacets") }}
        </button>
      </aside>

      <section class="results" :aria-busy="results.isFetching.value">
        <div class="toolbar">
          <h2 ref="heading" class="count-line" tabindex="-1" aria-live="polite">
            <template v-if="data">
              {{ t("search.results", total) }}
              <span class="muted small">{{ t("search.took", { ms: data.took }) }}</span>
            </template>
            <template v-else>{{ t("common.loading") }}</template>
          </h2>
          <p v-if="data?.ranking" class="muted small ranking-note">
            {{ t("search.rankedNewest", { n: data.ranking.candidates.toLocaleString() }) }}
          </p>
          <div class="row">
            <button type="button" class="btn sm filters-button" @click="filtersOpen = true">
              {{ filterCount ? t("search.filtersCount", { n: filterCount }) : t("search.filters") }}
            </button>
            <label class="row">
              <span class="muted small">{{ t("search.sort") }}</span>
              <select
                class="input select"
                :value="sortValue"
                @change="
                  go({ ...state, sort: ($event.target as HTMLSelectElement).value, page: 1 })
                "
              >
                <option
                  v-for="key in SORTS.filter((s) => s && (s !== 'relevance' || state.q))"
                  :key="key"
                  :value="key"
                >
                  {{ t(`search.sorts.${key}`) }}
                </option>
              </select>
            </label>
            <button type="button" class="btn ghost sm" @click="copyLink">
              {{ t("search.copyLink") }}
            </button>
            <button
              v-if="info.data.value?.sparql"
              type="button"
              class="btn ghost sm"
              :disabled="sparqlBusy"
              :title="t('search.showSparqlHelp')"
              @click="showSparql"
            >
              {{ t("search.showSparql") }}
            </button>
          </div>
        </div>

        <ul
          v-if="activeChips.length"
          class="active-filters"
          :aria-label="t('search.activeFilters')"
        >
          <li v-for="chip in activeChips" :key="`${chip.name}=${chip.value}`">
            <button
              type="button"
              class="active-chip"
              :title="t('search.removeFilter')"
              @click="go(removeChip(state, chip))"
            >
              {{ chipLabel(chip) }} <span aria-hidden="true">×</span>
            </button>
          </li>
        </ul>

        <div v-if="results.isError.value" class="notice error">
          {{ busy ? t("search.busy") : (results.error.value as Error).message }}
          <button class="btn sm" @click="results.refetch()">{{ t("common.retry") }}</button>
        </div>
        <section v-else-if="data && !data.hits.length" class="card empty">
          <h2>{{ t("search.noResults") }}</h2>
          <p class="muted">{{ t("search.noResultsHint") }}</p>
          <button v-if="activeChips.length" type="button" class="btn" @click="go(clearAll(state))">
            {{ t("search.clearAll") }}
          </button>
        </section>
        <div v-else-if="!data" class="skeletons" aria-hidden="true">
          <div v-for="n in 4" :key="n" class="skeleton" />
        </div>

        <ol v-if="data?.hits.length" class="hits" :start="data.startIndex + 1">
          <li v-for="hit in data.hits" :key="hit.id">
            <ResourceCard
              :hit="hit"
              :cards="landing.data.value?.cards[hit.type] ?? []"
              :field-label="fieldLabel"
              @filter="(name, value) => go(toggle(state, name, value))"
            />
          </li>
        </ol>

        <nav v-if="pages > 1" class="pager" :aria-label="t('search.pages')">
          <button class="btn sm" :disabled="state.page <= 1" @click="page(state.page - 1)">
            ← {{ t("search.previous") }}
          </button>
          <span class="muted small">{{ t("search.page", { page: state.page, pages }) }}</span>
          <button class="btn sm" :disabled="state.page >= pages" @click="page(state.page + 1)">
            {{ t("search.next") }} →
          </button>
        </nav>
      </section>
    </div>

    <FacetDialog
      v-if="dialogFacet"
      :name="dialogFacet"
      :title="facetTitle(dialogFacet)"
      :params="params"
      :selected="selected(dialogFacet)"
      @toggle="(value) => dialogFacet && go(toggle(state, dialogFacet, value))"
      @close="dialogFacet = null"
    />
  </div>
</template>

<style scoped>
.search-page {
  max-width: 1200px;
}
.searchbar {
  display: flex;
  gap: var(--fair-space-2);
}
.searchbar .box {
  flex: 1;
  min-width: 0;
}
.searchbar .input {
  width: 100%;
  font-size: 1.05rem;
  padding-block: 10px;
}
.hint {
  margin: var(--fair-space-1) 0 var(--fair-space-3);
}
.tabs {
  display: flex;
  gap: var(--fair-space-1);
  overflow-x: auto;
  border-bottom: 1px solid var(--fair-border);
  margin-bottom: var(--fair-space-4);
  scrollbar-width: thin;
}
.tab {
  border: 0;
  border-bottom: 3px solid transparent;
  background: none;
  color: var(--fair-text-muted);
  padding: var(--fair-space-2) var(--fair-space-3);
  font: inherit;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
}
.tab[aria-pressed="true"] {
  color: var(--fair-text);
  border-bottom-color: var(--tool-accent);
}
.tab .count {
  margin-left: 6px;
  font-size: var(--fair-text-xs, 0.75rem);
  font-variant-numeric: tabular-nums;
  color: var(--fair-text-muted);
}
.landing {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
  gap: var(--fair-space-5);
  align-items: center;
  margin-bottom: var(--fair-space-4);
}
.counters {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: var(--fair-space-2);
}
.counter {
  width: 100%;
  display: grid;
  gap: 2px;
  padding: var(--fair-space-3);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-md, 10px);
  background: var(--fair-surface);
  color: var(--fair-text);
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.counter:hover {
  border-color: var(--tool-accent);
}
.counter .n {
  font-size: 1.5rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.counter .what {
  color: var(--fair-text-muted);
  font-size: var(--fair-text-sm);
}
.explainer h2 {
  margin: 0 0 var(--fair-space-1);
  font-size: var(--fair-text-sm);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--fair-text-muted);
}
.explainer p {
  margin: 0;
  line-height: 1.5;
}
.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--fair-space-6);
  align-items: start;
}
.sidebar {
  position: sticky;
  top: calc(var(--fair-header-h) + 16px);
  max-height: calc(100vh - var(--fair-header-h) - 32px);
  overflow-y: auto;
  padding-right: var(--fair-space-2);
}
.sidebar-head {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
  margin-bottom: var(--fair-space-2);
}
.sidebar-head h2 {
  margin: 0;
  font-size: 1rem;
  flex: 1;
}
.sidebar-head .close,
.filters-button {
  display: none;
}
.active {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
  padding: var(--fair-space-2) 0;
  font-size: var(--fair-text-sm);
}
.dates {
  margin: 0;
  padding: var(--fair-space-3) 0;
  border: 0;
  border-top: 1px solid var(--fair-border);
  min-width: 0;
}
.dates legend {
  font-weight: 600;
  font-size: var(--fair-text-sm);
  padding: 0;
  margin-bottom: var(--fair-space-2);
}
.range {
  display: grid;
  grid-template-columns: 1fr 1fr auto 1fr;
  align-items: center;
  gap: 6px;
  margin-bottom: var(--fair-space-2);
}
.range .small {
  grid-column: 1 / -1;
  color: var(--fair-text-muted);
}
.range > span.small + label {
  grid-column: 1 / 3;
}
.range .input {
  width: 100%;
  min-width: 0;
  padding-inline: 6px;
  font-size: var(--fair-text-sm);
}
.more-facets {
  margin-top: var(--fair-space-2);
}
.results {
  min-width: 0;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--fair-space-2);
  margin-bottom: var(--fair-space-3);
}
.ranking-note {
  flex-basis: 100%;
  order: 3;
  margin: 0;
}
.count-line {
  margin: 0;
  font-size: 1rem;
}
.count-line:focus {
  outline: none;
}
.active-filters {
  list-style: none;
  margin: 0 0 var(--fair-space-3);
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.active-chip {
  border: 1px solid var(--tool-accent);
  border-radius: var(--fair-radius-pill);
  background: var(--tool-accent-tint);
  color: var(--fair-text);
  padding: 3px 10px;
  font: inherit;
  font-size: var(--fair-text-sm);
  cursor: pointer;
  max-width: 100%;
  overflow-wrap: anywhere;
  text-align: left;
}
.hits {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fair-space-3);
}
.skeletons {
  display: grid;
  gap: var(--fair-space-3);
}
.skeleton {
  height: 132px;
  border-radius: var(--fair-radius-lg);
  background: linear-gradient(
    90deg,
    var(--fair-border) 0%,
    var(--fair-surface) 50%,
    var(--fair-border) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite linear;
  opacity: 0.5;
}
@keyframes shimmer {
  to {
    background-position: -200% 0;
  }
}
.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--fair-space-4);
  margin-top: var(--fair-space-5);
}
.empty {
  text-align: center;
}
@media (max-width: 960px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .landing {
    grid-template-columns: minmax(0, 1fr);
  }
  .filters-button,
  .sidebar-head .close {
    display: inline-flex;
  }
  .sidebar {
    display: none;
  }
  .sidebar.open {
    display: block;
    position: fixed;
    inset: auto 0 0 0;
    top: auto;
    z-index: 40;
    max-height: 80vh;
    padding: var(--fair-space-4) 16px;
    background: var(--fair-surface);
    border-top: 1px solid var(--fair-border);
    border-radius: var(--fair-radius-lg) var(--fair-radius-lg) 0 0;
    box-shadow: var(--fair-shadow-4);
  }
}
</style>
