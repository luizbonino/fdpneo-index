<script setup lang="ts">
/**
 * Statistics on the index (TASKS 15.1): headline counts, counts over time
 * (daily, from the server's hourly snapshots), the splits now, registrations
 * by month and datasets by year of issue. Everything is counted as visitors see
 * the index. The period lives in the URL (?days=).
 *
 * Forms: stat tiles for the headline numbers; a line of active FDPs against the
 * listed total as context; small multiples (one sparkline per record type, each
 * on its own scale, since their sizes differ by orders of magnitude); ranked
 * horizontal bars in one hue for the splits; columns for months and years; a
 * meter list for conformance; a table for the largest FDPs.
 */
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ENTRY_STATES } from "@/api";
import { useStats } from "@/composables/queries";
import { currentLocale, localized } from "@/i18n";
import { absoluteTime, hostOf } from "@/utils/format";
import StatsChart from "@/components/stats/StatsChart.vue";
import StatsSparkline from "@/components/stats/StatsSparkline.vue";
import SparqlLink from "@/components/stats/SparqlLink.vue";
import StatsMap from "@/components/stats/StatsMap.vue";
import HealthStats from "@/components/health/HealthStats.vue";
import {
  DATASETS_BY_YEAR,
  FDPS_BY_STATE,
  LARGEST_FDPS,
  RECORDS_BY_TYPE,
} from "@/components/stats/sparqlQueries";

const KINDS = ["fdp", "catalog", "dataset", "series", "service", "distribution", "other"];
const PERIODS = [
  { days: 30, key: "d30" },
  { days: 90, key: "d90" },
  { days: 365, key: "d365" },
  { days: 3660, key: "all" },
] as const;

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const days = computed(() => {
  const asked = Number(route.query.days);
  return PERIODS.some((p) => p.days === asked) ? asked : 365;
});
function choose(value: number) {
  void router.replace({ query: { ...route.query, days: value === 365 ? undefined : value } });
}

const stats = useStats(days);
const body = computed(() => stats.data.value);

function number(value: number | null | undefined): string {
  return value == null ? "—" : new Intl.NumberFormat(currentLocale()).format(value);
}
function percent(part: number, whole: number): string {
  if (!whole) return "—";
  return new Intl.NumberFormat(currentLocale(), { style: "percent" }).format(part / whole);
}
function dayLabel(iso: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(new Date(`${iso}T00:00:00Z`));
}
function monthLabel(month: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${month}-01T00:00:00Z`));
}

// --- history ---------------------------------------------------------------------------

const history = computed(() => body.value?.history ?? { days: [], series: {} });
const historyDays = computed(() => history.value.days.map(dayLabel));
const hasHistory = computed(() => history.value.days.length >= 2);
function series(metric: string): (number | null)[] {
  return history.value.series[metric] ?? history.value.days.map(() => null);
}
/** Change over the period: the first count in it against the latest. */
function change(metric: string): string | null {
  const values = series(metric);
  const firstAt = values.findIndex((v) => v != null);
  const last = [...values].reverse().find((v) => v != null);
  const first = firstAt >= 0 ? values[firstAt] : null;
  if (first == null || last == null || first === last || !hasHistory.value) return null;
  const signed = new Intl.NumberFormat(currentLocale(), { signDisplay: "always" }).format(
    last - first,
  );
  return t("stats.change", { change: signed, date: dayLabel(history.value.days[firstAt]!) });
}

// --- headline numbers ------------------------------------------------------------------

const tiles = computed(() => {
  const b = body.value;
  if (!b) return [];
  const list = [
    { key: "fdps", value: b.fdps.total, metric: "fdps.total" },
    { key: "active", value: b.fdps.byState.ACTIVE ?? 0, metric: "fdps.state.ACTIVE" },
  ];
  if (b.records) {
    list.push(
      { key: "records", value: b.records.total, metric: "records.total" },
      { key: "datasets", value: b.records.byKind.dataset ?? 0, metric: "records.kind.dataset" },
    );
  }
  return list.map((tile) => ({ ...tile, change: change(tile.metric) }));
});

// --- splits now ------------------------------------------------------------------------

const states = computed(() => {
  const by = body.value?.fdps.byState ?? {};
  return ENTRY_STATES.map((s) => ({ label: t(`state.${s}`), value: by[s] ?? 0 }));
});
const kinds = computed(() => {
  const by = body.value?.records?.byKind ?? {};
  return KINDS.map((k) => ({ kind: k, label: t(`search.plural.${k}`), value: by[k] ?? 0 }))
    .filter(
      (k) => k.value > 0 || (history.value.series[`records.kind.${k.kind}`] ?? []).some(Boolean),
    )
    .sort((a, b) => b.value - a.value);
});
const conformance = computed(() =>
  (body.value?.conformance ?? []).map((share) => ({
    ...share,
    name: localized(share.title, share.pack),
    share: share.checked ? share.conforms / share.checked : 0,
  })),
);
const registrations = computed(() => body.value?.registrations ?? []);
const published = computed(() => body.value?.published ?? []);
const largest = computed(() => body.value?.largest ?? []);

// --- where (TASKS 15.4) ------------------------------------------------------------------

const SOURCES = ["admin", "stated", "publisher", "domain", "server"] as const;
const regionNames = computed(() => {
  try {
    return new Intl.DisplayNames([currentLocale()], { type: "region" });
  } catch {
    return null;
  }
});
function countryName(code: string): string {
  return regionNames.value?.of(code) ?? code;
}
const fdpCountries = computed(() => body.value?.fdps.byCountry ?? []);
const fdpCounts = computed(() =>
  Object.fromEntries(fdpCountries.value.map((c) => [c.country, c.fdps])),
);
const usedSources = computed(() =>
  SOURCES.filter((s) => fdpCountries.value.some((c) => (c.bySource[s] ?? 0) > 0)),
);
const recordCountries = computed(() => body.value?.records?.byCountry ?? []);
const recordCounts = computed(() =>
  Object.fromEntries(recordCountries.value.map((c) => [c.country, c.records])),
);
function spatialSearch(country: string) {
  const values = recordCountries.value.find((c) => c.country === country)?.values ?? [];
  return values.length ? { name: "search", query: { spatial: values } } : null;
}
</script>

<template>
  <div class="page statistics">
    <header class="page-head">
      <div>
        <h1>{{ t("stats.title") }}</h1>
        <p class="lead">{{ t("stats.lead") }}</p>
      </div>
      <div class="row periods" role="radiogroup" :aria-label="t('stats.period')">
        <label v-for="p in PERIODS" :key="p.days" class="period small">
          <input
            type="radio"
            name="period"
            :value="p.days"
            :checked="days === p.days"
            @change="choose(p.days)"
          />
          {{ t(`stats.periods.${p.key}`) }}
        </label>
      </div>
    </header>

    <p v-if="stats.isPending.value" class="muted">{{ t("common.loading") }}</p>
    <div v-else-if="stats.isError.value" class="card" role="alert">
      <p>{{ t("common.error") }}</p>
      <button class="btn" type="button" @click="stats.refetch()">{{ t("common.retry") }}</button>
    </div>

    <template v-else-if="body">
      <section class="tiles" :aria-label="t('stats.headline')">
        <div v-for="tile in tiles" :key="tile.key" class="card tile">
          <p class="tile-label">{{ t(`stats.tiles.${tile.key}`) }}</p>
          <p class="tile-value">{{ number(tile.value) }}</p>
          <p v-if="tile.change" class="tile-change small muted">{{ tile.change }}</p>
        </div>
      </section>

      <p class="muted small">
        {{ t("stats.counted", { time: absoluteTime(body.generatedAt) }) }}
        <template v-if="!body.records"> {{ t("stats.searchOff") }}</template>
      </p>

      <div class="grid">
        <section class="card wide" aria-labelledby="h-fdps-time">
          <h2 id="h-fdps-time">{{ t("stats.fdpsOverTime") }}</h2>
          <StatsChart
            v-if="hasHistory"
            kind="line"
            :title="t('stats.fdpsOverTime')"
            :label-header="t('stats.day')"
            :labels="historyDays"
            :series="[
              { label: t('stats.active'), data: series('fdps.state.ACTIVE') },
              { label: t('stats.listed'), data: series('fdps.total'), role: 'context' },
            ]"
          />
          <p v-else class="muted">
            {{
              history.days.length
                ? t("stats.historyStarts", { date: dayLabel(history.days[0]!) })
                : t("stats.noHistory")
            }}
          </p>
        </section>

        <section v-if="body.records" class="card wide" aria-labelledby="h-records-time">
          <h2 id="h-records-time">{{ t("stats.recordsOverTime") }}</h2>
          <p class="muted small">{{ t("stats.recordsOverTimeLead") }}</p>
          <ul class="multiples">
            <li v-for="k in kinds" :key="k.kind" class="multiple">
              <p class="multiple-label small">{{ k.label }}</p>
              <p class="multiple-value">{{ number(k.value) }}</p>
              <StatsSparkline
                v-if="hasHistory"
                :title="k.label"
                :labels="historyDays"
                :data="series(`records.kind.${k.kind}`)"
              />
              <p v-if="change(`records.kind.${k.kind}`)" class="small muted">
                {{ change(`records.kind.${k.kind}`) }}
              </p>
            </li>
          </ul>
        </section>

        <section class="card" aria-labelledby="h-states">
          <h2 id="h-states">{{ t("stats.fdpsByState") }}</h2>
          <StatsChart
            kind="barh"
            :title="t('stats.fdpsByState')"
            :label-header="t('stats.state')"
            :labels="states.map((s) => s.label)"
            :series="[{ label: t('stats.tiles.fdps'), data: states.map((s) => s.value) }]"
          />
          <SparqlLink :query="FDPS_BY_STATE" />
        </section>

        <section v-if="body.records" class="card" aria-labelledby="h-kinds">
          <h2 id="h-kinds">{{ t("stats.recordsByType") }}</h2>
          <StatsChart
            kind="barh"
            :title="t('stats.recordsByType')"
            :label-header="t('stats.type')"
            :labels="kinds.map((k) => k.label)"
            :series="[{ label: t('stats.tiles.records'), data: kinds.map((k) => k.value) }]"
          />
          <SparqlLink :query="RECORDS_BY_TYPE" />
        </section>

        <section class="card wide" aria-labelledby="h-where-fdps">
          <h2 id="h-where-fdps">{{ t("stats.whereFdps") }}</h2>
          <p class="muted small">{{ t("stats.whereFdpsLead") }}</p>
          <StatsMap
            v-if="fdpCountries.length"
            :counts="fdpCounts"
            :title="t('stats.whereFdps')"
            :describe="(n: number) => t('stats.fdpCount', n)"
          />
          <p class="muted small">
            {{ t("stats.unplaced", { n: number(body.fdps.unplaced) }, body.fdps.unplaced) }}
          </p>
          <details v-if="fdpCountries.length" class="table-view">
            <summary>{{ t("stats.showTable") }}</summary>
            <div class="scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th scope="col">{{ t("stats.country") }}</th>
                    <th scope="col" class="num">{{ t("stats.tiles.fdps") }}</th>
                    <th v-for="s in usedSources" :key="s" scope="col" class="num">
                      {{ t(`stats.sources.${s}`) }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in fdpCountries" :key="c.country">
                    <th scope="row">{{ countryName(c.country) }}</th>
                    <td class="num">{{ number(c.fdps) }}</td>
                    <td v-for="s in usedSources" :key="s" class="num">
                      {{ number(c.bySource[s] ?? 0) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>
          <dl class="sources small muted">
            <template v-for="s in SOURCES" :key="s">
              <dt>{{ t(`stats.sources.${s}`) }}</dt>
              <dd>{{ t(`stats.sourceHelp.${s}`) }}</dd>
            </template>
          </dl>
          <p v-if="usedSources.includes('server')" class="small muted">
            {{ t("stats.geoipCredit") }}
            <a href="https://db-ip.com" rel="noopener">DB-IP</a>
          </p>
        </section>

        <section v-if="body.records" class="card wide" aria-labelledby="h-where-data">
          <h2 id="h-where-data">{{ t("stats.whereData") }}</h2>
          <p class="muted small">{{ t("stats.whereDataLead") }}</p>
          <template v-if="recordCountries.length">
            <StatsMap
              :counts="recordCounts"
              :title="t('stats.whereData')"
              :describe="(n: number) => t('stats.recordCount', n)"
              :link="spatialSearch"
            />
            <details class="table-view">
              <summary>{{ t("stats.showTable") }}</summary>
              <div class="scroll">
                <table class="table">
                  <thead>
                    <tr>
                      <th scope="col">{{ t("stats.country") }}</th>
                      <th scope="col" class="num">{{ t("stats.tiles.records") }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="c in recordCountries" :key="c.country">
                      <th scope="row">
                        <router-link :to="spatialSearch(c.country) ?? { name: 'search' }">{{
                          countryName(c.country)
                        }}</router-link>
                      </th>
                      <td class="num">{{ number(c.records) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </template>
          <p v-else class="muted">{{ t("stats.noSpatial") }}</p>
        </section>

        <HealthStats v-if="body.health && body.health.fdps" :summary="body.health" />

        <section v-if="conformance.length" class="card wide" aria-labelledby="h-conformance">
          <h2 id="h-conformance">{{ t("stats.conformance") }}</h2>
          <p class="muted small">{{ t("stats.conformanceLead") }}</p>
          <ul class="meters">
            <li v-for="share in conformance" :key="share.pack" class="meter">
              <span class="meter-name">{{ share.name }}</span>
              <span
                class="meter-track"
                role="meter"
                :aria-label="share.name"
                aria-valuemin="0"
                :aria-valuemax="share.checked"
                :aria-valuenow="share.conforms"
                :aria-valuetext="percent(share.conforms, share.checked)"
              >
                <span class="meter-fill" :style="{ width: `${share.share * 100}%` }" />
              </span>
              <span class="meter-figure">
                <strong>{{ percent(share.conforms, share.checked) }}</strong>
                <span class="small muted">
                  {{
                    t("stats.conformanceRow", {
                      conforms: number(share.conforms),
                      checked: number(share.checked),
                    })
                  }}
                </span>
              </span>
            </li>
          </ul>
        </section>

        <section class="card" aria-labelledby="h-registrations">
          <h2 id="h-registrations">{{ t("stats.registrations") }}</h2>
          <p class="muted small">{{ t("stats.registrationsLead") }}</p>
          <StatsChart
            v-if="registrations.length"
            kind="column"
            :title="t('stats.registrations')"
            :label-header="t('stats.month')"
            :labels="registrations.map((m) => monthLabel(m.month))"
            :series="[{ label: t('stats.tiles.fdps'), data: registrations.map((m) => m.count) }]"
          />
          <p v-else class="muted">{{ t("stats.empty") }}</p>
        </section>

        <section v-if="body.records" class="card" aria-labelledby="h-published">
          <h2 id="h-published">{{ t("stats.published") }}</h2>
          <p class="muted small">{{ t("stats.publishedLead") }}</p>
          <StatsChart
            v-if="published.length"
            kind="column"
            :title="t('stats.published')"
            :label-header="t('stats.year')"
            :labels="published.map((y) => String(y.year))"
            :series="[{ label: t('stats.tiles.datasets'), data: published.map((y) => y.count) }]"
          />
          <p v-else class="muted">{{ t("stats.noIssued") }}</p>
          <SparqlLink :query="DATASETS_BY_YEAR" />
        </section>

        <section v-if="largest.length" class="card wide" aria-labelledby="h-largest">
          <h2 id="h-largest">{{ t("stats.largest") }}</h2>
          <div class="scroll">
            <table class="table">
              <thead>
                <tr>
                  <th scope="col">{{ t("stats.fdp") }}</th>
                  <th scope="col" class="num">{{ t("stats.tiles.records") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="fdp in largest" :key="fdp.uuid">
                  <td>
                    <router-link :to="{ name: 'entry', params: { uuid: fdp.uuid } }">{{
                      fdp.title || hostOf(fdp.clientUrl)
                    }}</router-link>
                    <span v-if="fdp.title" class="small muted host">{{
                      hostOf(fdp.clientUrl)
                    }}</span>
                  </td>
                  <td class="num">{{ number(fdp.records) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <SparqlLink :query="LARGEST_FDPS" />
        </section>
      </div>
      <p class="muted small">{{ t("stats.sparqlNote") }}</p>
    </template>
  </div>
</template>

<style scoped>
.statistics {
  display: grid;
  gap: var(--fair-space-5);
}
.periods {
  gap: var(--fair-space-3);
}
.period {
  display: inline-flex;
  align-items: center;
  gap: var(--fair-space-1);
  min-height: 44px;
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 150px), 1fr));
  gap: var(--fair-space-4);
}
.tile {
  padding: var(--fair-space-4) var(--fair-space-5);
}
.tile-label {
  color: var(--fair-text-muted);
  font-size: var(--fair-text-sm);
}
.tile-value {
  font-size: var(--fair-text-3xl);
  font-weight: 600;
  color: var(--fair-text-strong);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
  gap: var(--fair-space-5);
}
.grid > .card {
  min-width: 0;
  display: grid;
  gap: var(--fair-space-3);
  align-content: start;
}
.wide {
  grid-column: 1 / -1;
}
.multiples {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 140px), 1fr));
  gap: var(--fair-space-4);
}
.multiple {
  min-width: 0;
  border-top: 1px solid var(--fair-separator);
  padding-top: var(--fair-space-2);
}
.multiple-label {
  color: var(--fair-text-muted);
}
.multiple-value {
  font-size: var(--fair-text-xl);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.meters {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: var(--fair-space-3);
}
.meter {
  display: grid;
  grid-template-columns: minmax(8rem, 14rem) minmax(6rem, 1fr) auto;
  align-items: center;
  gap: var(--fair-space-3);
}
.meter-track {
  height: 10px;
  border-radius: 5px;
  background: var(--fair-separator);
  overflow: hidden;
}
.meter-fill {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: var(--tool-accent);
}
.meter-figure {
  display: inline-flex;
  gap: var(--fair-space-2);
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}
@media (max-width: 560px) {
  .meter {
    grid-template-columns: 1fr auto;
  }
  .meter-track {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
.scroll {
  overflow-x: auto;
}
.num {
  text-align: end;
  font-variant-numeric: tabular-nums;
}
.host {
  display: block;
  overflow-wrap: anywhere;
}
.table-view summary {
  cursor: pointer;
  color: var(--fair-text-muted);
  font-size: var(--fair-text-sm);
}
.sources {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px var(--fair-space-3);
  margin: 0;
}
.sources dt {
  font-weight: 600;
}
.sources dd {
  margin: 0;
}
@media (max-width: 560px) {
  .sources {
    grid-template-columns: 1fr;
  }
  .sources dd {
    margin-bottom: var(--fair-space-2);
  }
}
</style>
