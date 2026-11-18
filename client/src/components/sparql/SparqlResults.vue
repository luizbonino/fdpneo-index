<script setup lang="ts">
/**
 * One results panel for every way of querying (search design §2.2.4).
 *
 * SELECT: a sortable, paged table; IRIs as short, readable links (FDP graphs
 * by their title, known namespaces as CURIEs, the full IRI on hover) with a
 * "describe" action; literals with language or datatype badges. A chart view
 * appears when the result has a category and a number (the table stays the
 * accessible form). ASK: yes or no. CONSTRUCT/DESCRIBE: the graph as Turtle.
 * Downloads re-run the query with the format's media type, so the file is
 * exactly what the API returns.
 */
import { computed, defineAsyncComponent, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError } from "@/api/client";
import { runQuery, sparqlApi, type QueryOutcome } from "@/sparql/api";
import {
  chartShape,
  curie,
  shortIri,
  toMarkdown,
  toTable,
  type Cell,
  type Table,
} from "@/sparql/results";
import { toast } from "@/composables/feedback";

const ResultChart = defineAsyncComponent(() => import("./ResultChart.vue"));

const props = defineProps<{
  outcome: QueryOutcome | null;
  query: string;
  error: ApiError | null;
  busy: boolean;
  graphTitles: Record<string, string>;
  chartHint?: { type?: string | null; label?: string | null; value?: string | null } | null;
}>();
const emit = defineEmits<{ describe: [iri: string] }>();

const { t } = useI18n();
const PAGE = 100;
const view = ref<"table" | "chart" | "raw">("table");
const page = ref(0);
const sortColumn = ref(-1);
const sortDescending = ref(false);

const table = computed<Table | null>(() =>
  props.outcome?.kind === "select" && props.outcome.select ? toTable(props.outcome.select) : null,
);
const shape = computed(() =>
  table.value ? chartShape(table.value, props.chartHint ?? undefined) : null,
);

/** Labels for the IRIs in the result, fetched in one batch after each run. */
const labels = ref<Record<string, string>>({});
const titles = computed(() => ({ ...labels.value, ...props.graphTitles }));

async function loadLabels(current: Table | null) {
  labels.value = {};
  if (!current) return;
  const iris = new Set<string>();
  for (const row of current.rows) {
    for (const cell of row)
      if (cell.term?.type === "uri" && !props.graphTitles[cell.text]) iris.add(cell.text);
  }
  if (!iris.size) return;
  try {
    const found = (await sparqlApi.labels([...iris])).labels;
    if (table.value === current) labels.value = found;
  } catch {
    /* labels are a nicety: keep short IRIs */
  }
}

watch(
  () => props.outcome,
  () => {
    void loadLabels(table.value);
    page.value = 0;
    sortColumn.value = -1;
    const wantsChart = props.chartHint?.type && props.chartHint.type !== "none";
    view.value = wantsChart && shape.value ? "chart" : "table";
  },
);

const sorted = computed<Cell[][]>(() => {
  const rows = table.value?.rows ?? [];
  if (sortColumn.value < 0) return rows;
  const index = sortColumn.value;
  const direction = sortDescending.value ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[index]?.sortKey ?? "";
    const y = b[index]?.sortKey ?? "";
    if (typeof x === "number" && typeof y === "number") return (x - y) * direction;
    return String(x).localeCompare(String(y)) * direction;
  });
});
const pages = computed(() => Math.max(1, Math.ceil(sorted.value.length / PAGE)));
const visible = computed(() => sorted.value.slice(page.value * PAGE, (page.value + 1) * PAGE));

function sortBy(index: number) {
  if (sortColumn.value === index) sortDescending.value = !sortDescending.value;
  else {
    sortColumn.value = index;
    sortDescending.value = false;
  }
  page.value = 0;
}

function iriText(iri: string): string {
  return titles.value[iri] ?? shortIri(iri);
}

function badge(cell: Cell): string | null {
  const term = cell.term;
  if (!term || term.type !== "literal") return null;
  if (term["xml:lang"]) return `@${term["xml:lang"]}`;
  if (term.datatype) return curie(term.datatype) ?? term.datatype.split(/[#/]/).at(-1) ?? null;
  return null;
}

const FORMATS: { key: string; label: string; accept: string; ext: string; kinds: string[] }[] = [
  { key: "csv", label: "CSV", accept: "text/csv", ext: "csv", kinds: ["select"] },
  { key: "tsv", label: "TSV", accept: "text/tab-separated-values", ext: "tsv", kinds: ["select"] },
  {
    key: "json",
    label: "JSON",
    accept: "application/sparql-results+json",
    ext: "json",
    kinds: ["select", "ask"],
  },
  { key: "turtle", label: "Turtle", accept: "text/turtle", ext: "ttl", kinds: ["graph"] },
  {
    key: "ntriples",
    label: "N-Triples",
    accept: "application/n-triples",
    ext: "nt",
    kinds: ["graph"],
  },
  {
    key: "jsonld",
    label: "JSON-LD",
    accept: "application/ld+json",
    ext: "jsonld",
    kinds: ["graph"],
  },
];
const formats = computed(() =>
  FORMATS.filter((f) => props.outcome && f.kinds.includes(props.outcome.kind)),
);

async function download(format: (typeof FORMATS)[number]) {
  try {
    const result = await runQuery(props.query, format.accept);
    const blob = new Blob([result.text], { type: result.mediaType || format.accept });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `query-results.${format.ext}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  } catch (err) {
    toast((err as Error).message, "error");
  }
}

async function copyMarkdown() {
  if (!table.value) return;
  try {
    await navigator.clipboard.writeText(toMarkdown(table.value));
    toast(t("sparql.results.copied"));
  } catch {
    toast(t("sparql.results.copyFailed"), "error");
  }
}
</script>

<template>
  <section class="card results" :aria-busy="busy" aria-live="polite">
    <div v-if="busy" class="muted">{{ t("sparql.results.running") }}</div>

    <div v-else-if="error" class="notice error" role="alert">
      <strong>{{ t("sparql.results.failed") }}</strong>
      <span>{{ error.message }}</span>
      <span v-if="error.status === 408" class="small">{{ t("sparql.results.timeoutHint") }}</span>
      <span v-else-if="error.status === 429" class="small">{{ t("sparql.results.rateHint") }}</span>
    </div>

    <div v-else-if="!outcome" class="muted empty">{{ t("sparql.results.none") }}</div>

    <template v-else>
      <header class="bar">
        <p class="small muted">
          {{
            t("sparql.results.summary", {
              rows: outcome.rows.toLocaleString(),
              seconds: outcome.seconds.toFixed(2),
            })
          }}
          <strong v-if="outcome.truncated" class="warn-text">
            · {{ t("sparql.results.truncated", { rows: outcome.rows.toLocaleString() }) }}
          </strong>
        </p>
        <div
          v-if="outcome.kind === 'select'"
          class="seg"
          role="tablist"
          :aria-label="t('sparql.results.view')"
        >
          <button role="tab" :aria-selected="view === 'table'" @click="view = 'table'">
            {{ t("sparql.results.table") }}
          </button>
          <button v-if="shape" role="tab" :aria-selected="view === 'chart'" @click="view = 'chart'">
            {{ t("sparql.results.chart") }}
          </button>
          <button role="tab" :aria-selected="view === 'raw'" @click="view = 'raw'">
            {{ t("sparql.results.raw") }}
          </button>
        </div>
        <div class="row downloads">
          <span class="small muted">{{ t("sparql.results.download") }}</span>
          <button v-for="f in formats" :key="f.key" class="btn sm" @click="download(f)">
            {{ f.label }}
          </button>
          <button v-if="table" class="btn sm ghost" @click="copyMarkdown">
            {{ t("sparql.results.copyMarkdown") }}
          </button>
        </div>
      </header>

      <p v-if="outcome.kind === 'ask'" class="ask">
        {{ outcome.ask ? t("sparql.results.yes") : t("sparql.results.no") }}
      </p>

      <pre v-else-if="outcome.kind === 'graph'" class="raw">{{ outcome.text }}</pre>

      <template v-else-if="table">
        <p v-if="!table.rows.length" class="muted empty">{{ t("sparql.results.noRows") }}</p>
        <ResultChart
          v-else-if="view === 'chart' && shape"
          :table="table"
          :shape="shape"
          :type="chartHint?.type ?? 'bar'"
          :graph-titles="titles"
        />
        <pre v-else-if="view === 'raw'" class="raw">{{ outcome.text }}</pre>
        <div v-else class="scroll">
          <table class="table grid">
            <thead>
              <tr>
                <th
                  v-for="(column, index) in table.columns"
                  :key="column"
                  scope="col"
                  :aria-sort="
                    sortColumn === index ? (sortDescending ? 'descending' : 'ascending') : 'none'
                  "
                >
                  <button class="sort" @click="sortBy(index)">
                    ?{{ column }}
                    <span aria-hidden="true">{{
                      sortColumn === index ? (sortDescending ? "▾" : "▴") : ""
                    }}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, r) in visible" :key="r">
                <td v-for="(cell, c) in row" :key="c">
                  <template v-if="cell.term?.type === 'uri'">
                    <span class="iri">
                      <a
                        :href="cell.text"
                        target="_blank"
                        rel="noopener noreferrer"
                        :title="cell.text"
                        >{{ iriText(cell.text) }}</a
                      >
                      <button
                        class="describe"
                        :title="t('sparql.results.describe')"
                        :aria-label="t('sparql.results.describeIri', { iri: cell.text })"
                        @click="emit('describe', cell.text)"
                      >
                        ⌕
                      </button>
                    </span>
                  </template>
                  <template v-else>
                    <span class="literal">{{ cell.text }}</span>
                    <span
                      v-if="badge(cell)"
                      class="badge mono"
                      :title="cell.term?.type === 'literal' ? cell.term.datatype : ''"
                      >{{ badge(cell) }}</span
                    >
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
          <nav v-if="pages > 1" class="pager row" :aria-label="t('sparql.results.pages')">
            <button class="btn sm" :disabled="page === 0" @click="page -= 1">←</button>
            <span class="small muted">{{ t("directory.page", { page: page + 1, pages }) }}</span>
            <button class="btn sm" :disabled="page + 1 >= pages" @click="page += 1">→</button>
          </nav>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.results {
  display: grid;
  gap: var(--fair-space-3);
  min-width: 0;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fair-space-2) var(--fair-space-4);
}
.bar p {
  margin: 0;
  flex: 1 1 auto;
}
.warn-text {
  color: var(--fair-warning);
}
.seg {
  display: inline-flex;
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-md);
  overflow: hidden;
}
.seg button {
  border: 0;
  background: transparent;
  padding: 6px 12px;
  color: var(--fair-text);
  cursor: pointer;
}
.seg button[aria-selected="true"] {
  background: var(--tool-accent-tint);
  color: var(--tool-accent);
  font-weight: var(--fair-weight-semibold);
}
.downloads {
  flex-wrap: wrap;
  gap: 6px;
}
.ask {
  font-size: 2rem;
  font-weight: var(--fair-weight-semibold);
  margin: var(--fair-space-2) 0;
}
.raw {
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  padding: var(--fair-space-3);
  background: var(--fair-code-bg);
  border-radius: var(--fair-radius-md);
  font-size: 12.5px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.scroll {
  overflow-x: auto;
  max-width: 100%;
}
.grid {
  min-width: 100%;
}
.grid thead th {
  position: sticky;
  top: 0;
  background: var(--fair-surface);
  z-index: 1;
}
.sort {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  font-family: var(--fair-font-mono);
  text-transform: none;
}
.grid td {
  vertical-align: top;
  max-width: 420px;
  overflow-wrap: anywhere;
}
.iri {
  display: inline-flex;
  gap: 4px;
  align-items: baseline;
}
.describe {
  border: 0;
  background: none;
  color: var(--fair-text-muted);
  cursor: pointer;
  padding: 0 2px;
  border-radius: 4px;
}
.describe:hover,
.describe:focus-visible {
  color: var(--tool-accent);
  background: var(--tool-accent-tint);
}
.badge {
  margin-left: 6px;
  font-size: 11px;
  color: var(--fair-text-muted);
  background: var(--fair-code-bg);
  border-radius: 4px;
  padding: 1px 4px;
}
.pager {
  justify-content: center;
  padding-top: var(--fair-space-3);
}
.empty {
  padding: var(--fair-space-4) 0;
}
</style>
