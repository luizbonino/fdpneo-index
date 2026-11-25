<script setup lang="ts">
/**
 * Metadata health over the network (TASKS 15.8): how far the FDPs' metadata
 * reaches (FDPcrawleR's drop-off), how many are fine, the findings by how
 * many FDPs have them, and a sortable table of every FDP with a CSV download.
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import {
  api,
  HEALTH_FINDINGS,
  HEALTH_LEVELS,
  HEALTH_NOTES,
  type HealthRow,
  type Statistics,
} from "@/api";
import { session } from "@/auth/session";
import { currentLocale } from "@/i18n";
import { hostOf } from "@/utils/format";
import StatsChart from "@/components/stats/StatsChart.vue";

const props = defineProps<{ summary: NonNullable<Statistics["health"]> }>();
const { t } = useI18n();

const number = (n: number) => new Intl.NumberFormat(currentLocale()).format(n);

/** FDPs whose metadata reaches at least each level. */
const reaching = computed(() =>
  HEALTH_LEVELS.map((level, i) => ({
    label: t(`health.levels.${level}`),
    value: HEALTH_LEVELS.slice(i).reduce((sum, l) => sum + (props.summary.byReach[l] ?? 0), 0),
  })),
);
const findings = computed(() =>
  HEALTH_FINDINGS.map((code) => ({
    label: t(`health.findings.${code}.title`),
    value: props.summary.findings[code] ?? 0,
  })),
);

const notes = computed(() =>
  HEALTH_NOTES.map((code) => ({
    code,
    label: t(`health.findings.${code}.title`),
    value: props.summary.notes?.[code] ?? 0,
  })),
);

const table = useQuery({
  queryKey: computed(() => ["health-table", session.isAdmin]),
  queryFn: api.healthTable,
  staleTime: 60_000,
});

type Column = "fdp" | "reach" | "pages" | "findings" | "failed";
const sortBy = ref<Column>("fdp");
const ascending = ref(true);
function sort(column: Column) {
  if (sortBy.value === column) ascending.value = !ascending.value;
  else {
    sortBy.value = column;
    ascending.value = column === "fdp";
  }
}
function name(row: HealthRow): string {
  return row.title || hostOf(row.clientUrl);
}
function findingsOf(row: HealthRow): number {
  return Object.values(row.findings).reduce((a, b) => a + b, 0);
}
function value(row: HealthRow, column: Column): string | number {
  switch (column) {
    case "fdp":
      return name(row).toLowerCase();
    case "reach":
      return row.reach ? HEALTH_LEVELS.indexOf(row.reach as never) : -1;
    case "pages":
      return row.pages;
    case "findings":
      return findingsOf(row);
    case "failed":
      return row.failed;
  }
}
const rows = computed(() => {
  const list = [...(table.data.value ?? [])];
  const dir = ascending.value ? 1 : -1;
  return list.sort((a, b) => {
    const x = value(a, sortBy.value);
    const y = value(b, sortBy.value);
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
});
function ariaSort(column: Column) {
  if (sortBy.value !== column) return "none";
  return ascending.value ? "ascending" : "descending";
}

function csvCell(text: string | number): string {
  const s = String(text);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function downloadCsv() {
  const header = [
    "fdp",
    "url",
    "reach",
    "status",
    "pages",
    ...HEALTH_LEVELS,
    ...HEALTH_FINDINGS,
    ...HEALTH_NOTES,
    "failed",
    "networkRequirementsMissed",
    "stoppedAtLimit",
  ];
  const lines = [header.join(",")];
  for (const row of rows.value) {
    lines.push(
      [
        name(row),
        row.clientUrl,
        row.reach ?? "",
        row.status,
        row.pages,
        ...HEALTH_LEVELS.map((l) => row.levels[l] ?? 0),
        ...HEALTH_FINDINGS.map((f) => row.findings[f] ?? 0),
        ...HEALTH_NOTES.map((f) => row.notes?.[f] ?? 0),
        row.failed,
        row.requirements ?? 0,
        row.partial ? "yes" : "no",
      ]
        .map(csvCell)
        .join(","),
    );
  }
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "fdp-metadata-health.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}
const TONE = { fine: "ok", findings: "warn", unreadable: "bad" } as const;
</script>

<template>
  <section class="card wide stack" aria-labelledby="h-health">
    <h2 id="h-health">{{ t("health.title") }}</h2>
    <p class="muted small">{{ t("health.statsLead") }}</p>
    <ul class="status-tiles">
      <li v-for="status in ['fine', 'findings', 'unreadable'] as const" :key="status">
        <span :class="['chip', TONE[status]]">{{ t(`health.status.${status}`) }}</span>
        <span class="value">{{ number(summary.byStatus[status] ?? 0) }}</span>
      </li>
      <li v-if="summary.missingRequirements">
        <span class="chip warn">{{ t("health.missingRequirements") }}</span>
        <span class="value">{{ number(summary.missingRequirements) }}</span>
      </li>
      <li v-if="summary.partial">
        <span class="chip info">{{ t("health.partial") }}</span>
        <span class="value">{{ number(summary.partial) }}</span>
      </li>
    </ul>
    <div class="two">
      <div>
        <h3>{{ t("health.reaching") }}</h3>
        <StatsChart
          kind="barh"
          :title="t('health.reaching')"
          :label-header="t('health.level')"
          :labels="reaching.map((r) => r.label)"
          :series="[{ label: t('stats.tiles.fdps'), data: reaching.map((r) => r.value) }]"
          :height="220"
        />
      </div>
      <div>
        <h3>{{ t("health.withFinding") }}</h3>
        <StatsChart
          kind="barh"
          :title="t('health.withFinding')"
          :label-header="t('health.finding')"
          :labels="findings.map((f) => f.label)"
          :series="[{ label: t('stats.tiles.fdps'), data: findings.map((f) => f.value) }]"
          :height="220"
        />
        <p class="small muted notes">
          {{ t("health.withNote") }}:
          <template v-for="(note, i) in notes" :key="note.code">
            {{ i ? " · " : "" }}{{ note.label }} {{ number(note.value) }}
          </template>
        </p>
      </div>
    </div>

    <div class="row">
      <h3>{{ t("health.table") }}</h3>
      <span class="spacer" />
      <button type="button" class="btn sm" :disabled="!rows.length" @click="downloadCsv">
        {{ t("health.csv") }}
      </button>
    </div>
    <div class="scroll">
      <table class="table">
        <caption class="visually-hidden">
          {{
            t("health.table")
          }}
        </caption>
        <thead>
          <tr>
            <th
              v-for="column in ['fdp', 'reach', 'pages', 'findings', 'failed'] as const"
              :key="column"
              scope="col"
              :class="{ num: column !== 'fdp' && column !== 'reach' }"
              :aria-sort="ariaSort(column)"
            >
              <button type="button" class="sort" @click="sort(column)">
                {{ t(`health.columns.${column}`) }}
                <span aria-hidden="true">{{
                  sortBy === column ? (ascending ? "▲" : "▼") : ""
                }}</span>
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.uuid">
            <td>
              <router-link :to="{ name: 'entry', params: { uuid: row.uuid } }">{{
                name(row)
              }}</router-link>
              <span :class="['chip', 'sm', TONE[row.status as keyof typeof TONE] ?? 'info']">{{
                t(`health.status.${row.status}`)
              }}</span>
            </td>
            <td>{{ row.reach ? t(`health.levels.${row.reach}`) : "—" }}</td>
            <td class="num">{{ number(row.pages) }}{{ row.partial ? "+" : "" }}</td>
            <td class="num">{{ number(findingsOf(row)) }}</td>
            <td class="num">{{ number(row.failed) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.status-tiles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fair-space-5);
  margin: 0;
  padding: 0;
  list-style: none;
}
.status-tiles li {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
}
.status-tiles .value {
  font-size: var(--fair-text-xl);
  font-weight: var(--fair-weight-semibold);
  font-variant-numeric: tabular-nums;
}
.notes {
  margin-top: var(--fair-space-2);
}
.two {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--fair-space-6);
}
.scroll {
  max-height: 28rem;
  overflow: auto;
}
.sort {
  all: unset;
  cursor: pointer;
  white-space: nowrap;
}
.sort:focus-visible {
  outline: 2px solid var(--tool-accent);
  outline-offset: 2px;
}
td .chip {
  margin-left: var(--fair-space-2);
  height: 20px;
  font-size: var(--fair-text-xs);
}
@media (max-width: 900px) {
  .two {
    grid-template-columns: 1fr;
  }
}
</style>
