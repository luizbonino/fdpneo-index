<script setup lang="ts">
/**
 * Query the harvested metadata with SPARQL (search design §2.2.4).
 *
 * Three ways in, one results panel: the query library (questions with forms),
 * the builder (a custom query from forms), and the editor (SPARQL with
 * autocomplete from the data). History keeps recent runs in this browser.
 * The page state is in the URL: `tab`, `example` + `p.<name>`, `q` (an
 * encoded query), `b` (a builder model), so any view can be shared.
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { runQuery, sparqlApi, type Example, type QueryOutcome } from "@/sparql/api";
import { decodeModel, emptyModel, encodeModel, type BuilderModel } from "@/sparql/builder";
import {
  clearHistory,
  decodeQuery,
  encodeQuery,
  history,
  remember,
  togglePin,
} from "@/sparql/history";
import { relativeTime } from "@/utils/format";
import { toast } from "@/composables/feedback";
import QueryLibrary from "@/components/sparql/QueryLibrary.vue";
import QueryBuilder from "@/components/sparql/QueryBuilder.vue";
import SparqlEditor from "@/components/sparql/SparqlEditor.vue";
import SparqlResults from "@/components/sparql/SparqlResults.vue";
import CopyField from "@/components/CopyField.vue";
import { useServiceInfo } from "@/composables/queries";

type Tab = "library" | "builder" | "editor" | "history";
const TABS: Tab[] = ["library", "builder", "editor", "history"];

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const info = useServiceInfo();

const schema = useQuery({
  queryKey: ["sparql", "schema"],
  queryFn: sparqlApi.schema,
  staleTime: 60_000,
});
const library = useQuery({
  queryKey: ["sparql", "examples"],
  queryFn: sparqlApi.examples,
  staleTime: Infinity,
});

const STARTER = `# Datasets and the FDP they come from. Ctrl/Cmd+Enter runs the query.
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>

SELECT ?dataset ?title ?fdp
WHERE {
  GRAPH ?fdp {
    ?dataset a dcat:Dataset .
    OPTIONAL { ?dataset dct:title ?title }
  }
}
LIMIT 100
`;

const initialTab = String(route.query.tab ?? "");
const tab = ref<Tab>(TABS.includes(initialTab as Tab) ? (initialTab as Tab) : "library");
const text = ref(STARTER);
const builder = ref<BuilderModel>(
  typeof route.query.b === "string" ? (decodeModel(route.query.b) ?? emptyModel()) : emptyModel(),
);
const exampleId = ref<string | null>(
  typeof route.query.example === "string" ? route.query.example : null,
);
const exampleValues = ref<Record<string, string>>(
  Object.fromEntries(
    Object.entries(route.query)
      .filter(([k, v]) => k.startsWith("p.") && typeof v === "string")
      .map(([k, v]) => [k.slice(2), String(v)]),
  ),
);

const busy = ref(false);
const outcome = ref<QueryOutcome | null>(null);
const error = ref<ApiError | null>(null);
const ranQuery = ref("");
const chartHint = ref<{
  type?: string | null;
  label?: string | null;
  value?: string | null;
} | null>(null);
const results = ref<HTMLElement>();
const editor = ref<InstanceType<typeof SparqlEditor>>();

const graphTitles = computed<Record<string, string>>(() =>
  Object.fromEntries((schema.data.value?.graphs ?? []).map((g) => [g.iri, g.title])),
);
const endpoint = computed(() => {
  const value = info.data.value?.sparql?.endpoint;
  return typeof value === "string" ? value : "";
});
const curl = computed(
  () =>
    `curl -H 'Accept: text/csv' --data-urlencode 'query=SELECT * WHERE { ?s a <http://www.w3.org/ns/dcat#Dataset> } LIMIT 10' ${endpoint.value}`,
);

function setUrl(extra: Record<string, string | undefined>) {
  const query: Record<string, string> = { tab: tab.value };
  for (const [k, v] of Object.entries(extra)) if (v !== undefined) query[k] = v;
  void router.replace({ query });
}

watch(tab, (value) => {
  if (value === "builder") setUrl({ b: encodeModel(builder.value) });
  else if (value === "library" && exampleId.value)
    setUrl({ example: exampleId.value, ...prefixed(exampleValues.value) });
  else setUrl({});
});

watch(
  builder,
  (model) => {
    if (tab.value === "builder") setUrl({ b: encodeModel(model) });
  },
  { deep: true },
);

function prefixed(values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [`p.${k}`, v]));
}

async function run(query: string, hint: typeof chartHint.value = null) {
  if (!query.trim() || busy.value) return;
  busy.value = true;
  error.value = null;
  ranQuery.value = query;
  chartHint.value = hint;
  try {
    outcome.value = await runQuery(query);
    remember(query, outcome.value.rows);
  } catch (err) {
    outcome.value = null;
    error.value = err instanceof ApiError ? err : new ApiError(0, (err as Error).message);
  } finally {
    busy.value = false;
  }
  await nextTick();
  results.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runEditor() {
  if (tab.value === "editor") setUrl({ q: await encodeQuery(text.value) });
  await run(text.value);
}

function selectExample(id: string | null, values: Record<string, string>) {
  exampleId.value = id;
  exampleValues.value = values;
  setUrl(id ? { example: id, ...prefixed(values) } : {});
}

function runExample(query: string, example: Example) {
  void run(query, example.chart ?? null);
}

function openInEditor(query: string) {
  text.value = query;
  tab.value = "editor";
  void nextTick(() => editor.value?.focus());
}

function describe(iri: string) {
  const query = `DESCRIBE <${iri}>`;
  openInEditor(query);
  void run(query);
}

async function share() {
  const encoded = await encodeQuery(text.value);
  const url = new URL(
    router.resolve({ name: "sparql", query: { tab: "editor", q: encoded } }).href,
    location.origin,
  );
  try {
    await navigator.clipboard.writeText(url.toString());
    toast(t("sparql.editor.linkCopied"));
  } catch {
    toast(url.toString());
  }
}

function addPrefixes() {
  const added = editor.value?.addMissingPrefixes() ?? 0;
  toast(added ? t("sparql.editor.prefixesAdded", added) : t("sparql.editor.prefixesNone"));
}

function insertGraph(event: Event) {
  const select = event.target as HTMLSelectElement;
  if (select.value) editor.value?.insertAtCursor(`GRAPH <${select.value}> {\n    \n  }`);
  select.value = "";
}

function onTabKey(event: KeyboardEvent, index: number) {
  const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
  if (!delta) return;
  const next = TABS[(index + delta + TABS.length) % TABS.length] ?? "library";
  tab.value = next;
  void nextTick(() => document.getElementById(`tab-${next}`)?.focus());
}

onMounted(async () => {
  if (typeof route.query.q === "string") {
    const decoded = await decodeQuery(route.query.q);
    if (decoded) {
      text.value = decoded;
      tab.value = "editor";
    } else toast(t("sparql.editor.badLink"), "error");
  }
});
</script>

<template>
  <div class="page sparql">
    <header class="page-head">
      <div>
        <h1>{{ t("sparql.title") }}</h1>
        <p class="lead">{{ t("sparql.lead") }}</p>
      </div>
    </header>

    <details v-if="endpoint" class="card endpoint">
      <summary>{{ t("sparql.endpoint.title") }}</summary>
      <p class="small muted">{{ t("sparql.endpoint.lead") }}</p>
      <CopyField :value="endpoint" :label="t('sparql.endpoint.url')" />
      <CopyField :value="curl" :label="t('sparql.endpoint.curl')" />
      <p class="small muted">
        {{
          t("sparql.endpoint.limits", {
            seconds: info.data.value?.sparql?.timeoutSeconds,
            rows: Number(info.data.value?.sparql?.maxRows ?? 0).toLocaleString(),
            perMinute: info.data.value?.sparql?.perMinute,
          })
        }}
      </p>
    </details>

    <div class="tabs" role="tablist" :aria-label="t('sparql.title')">
      <button
        v-for="(name, index) in TABS"
        :id="`tab-${name}`"
        :key="name"
        role="tab"
        :aria-selected="tab === name"
        :aria-controls="`panel-${name}`"
        :tabindex="tab === name ? 0 : -1"
        @click="tab = name"
        @keydown="onTabKey($event, index)"
      >
        {{ t(`sparql.tabs.${name}`) }}
        <span v-if="name === 'history' && history.length" class="count">{{ history.length }}</span>
      </button>
    </div>

    <section
      v-show="tab === 'library'"
      id="panel-library"
      role="tabpanel"
      aria-labelledby="tab-library"
      class="card"
    >
      <p class="small muted intro">{{ t("sparql.library.lead") }}</p>
      <QueryLibrary
        :library="library.data.value ?? null"
        :schema="schema.data.value ?? null"
        :selected="exampleId"
        :initial-values="exampleValues"
        :busy="busy"
        @select="selectExample"
        @run="runExample"
        @edit="openInEditor"
      />
    </section>

    <section
      v-show="tab === 'builder'"
      id="panel-builder"
      role="tabpanel"
      aria-labelledby="tab-builder"
      class="card"
    >
      <p class="small muted intro">{{ t("sparql.builder.lead") }}</p>
      <QueryBuilder
        v-model="builder"
        :schema="schema.data.value ?? null"
        :busy="busy"
        @run="run"
        @edit="openInEditor"
      />
    </section>

    <section
      v-show="tab === 'editor'"
      id="panel-editor"
      role="tabpanel"
      aria-labelledby="tab-editor"
      class="card editor-panel"
    >
      <div class="toolbar row">
        <button class="btn primary" :disabled="busy" @click="runEditor">
          {{ t("sparql.run") }} <kbd>⌘↵</kbd>
        </button>
        <button class="btn sm" @click="addPrefixes">{{ t("sparql.editor.addPrefixes") }}</button>
        <select
          class="input select sm"
          :aria-label="t('sparql.editor.insertGraph')"
          @change="insertGraph"
        >
          <option value="">{{ t("sparql.editor.insertGraph") }}</option>
          <option v-for="g in schema.data.value?.graphs ?? []" :key="g.iri" :value="g.iri">
            {{ g.title }}
          </option>
        </select>
        <button class="btn sm ghost" @click="share">{{ t("sparql.editor.share") }}</button>
        <button class="btn sm ghost" @click="text = STARTER">{{ t("sparql.editor.reset") }}</button>
      </div>
      <div class="editor-grid">
        <SparqlEditor
          ref="editor"
          v-model="text"
          :schema="schema.data.value ?? null"
          :label="t('sparql.editor.label')"
          :placeholder="t('sparql.editor.placeholder')"
          @run="runEditor"
        />
        <aside class="vocab" :aria-label="t('sparql.editor.inData')">
          <h3 class="small">{{ t("sparql.editor.classes") }}</h3>
          <ul>
            <li v-for="c in (schema.data.value?.classes ?? []).slice(0, 12)" :key="c.iri">
              <button class="term" :title="c.iri" @click="editor?.insertAtCursor(c.curie)">
                <span class="mono">{{ c.curie }}</span><span class="muted small">{{
                  c.count.toLocaleString()
                }}</span>
              </button>
            </li>
          </ul>
          <h3 class="small">{{ t("sparql.editor.properties") }}</h3>
          <ul>
            <li v-for="p in (schema.data.value?.properties ?? []).slice(0, 25)" :key="p.iri">
              <button class="term" :title="p.iri" @click="editor?.insertAtCursor(p.curie)">
                <span class="mono">{{ p.curie }}</span><span class="muted small">{{
                  p.count.toLocaleString()
                }}</span>
              </button>
            </li>
          </ul>
          <p class="small muted">{{ t("sparql.editor.hint") }}</p>
        </aside>
      </div>
    </section>

    <section
      v-show="tab === 'history'"
      id="panel-history"
      role="tabpanel"
      aria-labelledby="tab-history"
      class="card"
    >
      <p v-if="!history.length" class="muted">{{ t("sparql.history.empty") }}</p>
      <ul v-else class="history">
        <li v-for="item in history" :key="item.query + item.at">
          <pre class="mono small">{{
            item.query
              .replace(/^PREFIX[^\n]*\n/gm, "")
              .trim()
              .slice(0, 400)
          }}</pre>
          <div class="row meta">
            <span class="small muted">{{ relativeTime(item.at) }}</span>
            <span v-if="item.rows !== null" class="small muted"
              >· {{ t("sparql.history.rows", { n: item.rows }) }}</span
            >
            <span class="spacer" />
            <button
              class="btn sm ghost"
              :aria-pressed="!!item.pinned"
              @click="togglePin(item.query)"
            >
              {{ item.pinned ? t("sparql.history.unpin") : t("sparql.history.pin") }}
            </button>
            <button class="btn sm" @click="openInEditor(item.query)">
              {{ t("sparql.library.openInEditor") }}
            </button>
            <button class="btn sm primary" :disabled="busy" @click="run(item.query)">
              {{ t("sparql.run") }}
            </button>
          </div>
        </li>
      </ul>
      <button v-if="history.length" class="btn sm ghost" @click="clearHistory">
        {{ t("sparql.history.clear") }}
      </button>
    </section>

    <div ref="results" class="results-anchor">
      <SparqlResults
        :outcome="outcome"
        :query="ranQuery"
        :error="error"
        :busy="busy"
        :graph-titles="graphTitles"
        :chart-hint="chartHint"
        @describe="describe"
      />
    </div>
  </div>
</template>

<style scoped>
.sparql {
  display: grid;
  gap: var(--fair-space-4);
}
.endpoint summary {
  cursor: pointer;
  font-weight: var(--fair-weight-semibold);
}
.endpoint {
  display: grid;
  gap: var(--fair-space-2);
}
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--fair-border);
  overflow-x: auto;
}
.tabs button {
  border: 0;
  background: none;
  padding: 10px 16px;
  font: inherit;
  font-weight: var(--fair-weight-medium);
  color: var(--fair-text);
  border-bottom: 3px solid transparent;
  cursor: pointer;
  white-space: nowrap;
}
.tabs button[aria-selected="true"] {
  color: var(--tool-accent);
  border-bottom-color: var(--tool-accent);
}
.tabs .count {
  margin-left: 4px;
  font-size: 11px;
  color: var(--fair-text-muted);
}
.intro {
  margin-top: 0;
}
.toolbar {
  flex-wrap: wrap;
  gap: var(--fair-space-2);
  margin-bottom: var(--fair-space-3);
}
.toolbar .select {
  width: auto;
  max-width: 240px;
}
kbd {
  font-size: 11px;
  opacity: 0.8;
  margin-left: 4px;
}
.editor-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: var(--fair-space-4);
}
.vocab {
  max-height: 60vh;
  overflow-y: auto;
  border-left: 1px solid var(--fair-separator);
  padding-left: var(--fair-space-3);
}
.vocab h3 {
  margin: var(--fair-space-2) 0 4px;
  color: var(--fair-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.vocab ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.term {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  border: 0;
  background: none;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--fair-text);
  text-align: left;
  font-size: 12.5px;
}
.term:hover,
.term:focus-visible {
  background: var(--tool-accent-tint);
}
.history {
  list-style: none;
  margin: 0 0 var(--fair-space-3);
  padding: 0;
  display: grid;
  gap: var(--fair-space-3);
}
.history li {
  border-bottom: 1px solid var(--fair-separator);
  padding-bottom: var(--fair-space-3);
}
.history pre {
  margin: 0 0 var(--fair-space-2);
  white-space: pre-wrap;
  max-height: 8em;
  overflow: hidden;
}
.meta {
  flex-wrap: wrap;
  gap: var(--fair-space-2);
}
.spacer {
  flex: 1;
}
.results-anchor {
  scroll-margin-top: calc(var(--fair-header-h) + 12px);
}
@media (max-width: 900px) {
  .editor-grid {
    grid-template-columns: 1fr;
  }
  .vocab {
    border-left: 0;
    padding-left: 0;
    max-height: 220px;
  }
}
</style>
