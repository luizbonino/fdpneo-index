<script setup lang="ts">
/**
 * The network's benchmarks on an FDP (TASKS 15.10): per benchmark, how many of
 * the FDP's records meet each condition, the result on the FDP itself, and
 * the records that do not meet a condition, with the runner's guidance and
 * the tests' logs. The network's own verdict, beside the metadata health and
 * never part of it. Administrators can run a benchmark on one record now.
 * Hidden when the profile names no benchmark, or results are for
 * administrators and the reader is not one.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  api,
  type BenchmarkResult,
  type BenchmarkResultDetail,
  type BenchmarkResultPage,
  type EntryBenchmark,
} from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { toast } from "@/composables/feedback";
import { currentLocale } from "@/i18n";
import { absoluteTime, relativeTime } from "@/utils/format";

const props = defineProps<{ uuid: string; clientUrl: string }>();
const { t } = useI18n();
const client = useQueryClient();
const number = (n: number) => new Intl.NumberFormat(currentLocale()).format(n);

const key = computed(() => ["entry", props.uuid, "benchmarks", session.isAdmin]);
const benchmarks = useQuery({
  queryKey: key,
  queryFn: () => api.entryBenchmarks(props.uuid),
  retry: false,
});
const hidden = computed(
  () =>
    (benchmarks.error.value instanceof ApiError &&
      [401, 403, 404].includes(benchmarks.error.value.status)) ||
    (benchmarks.data.value?.benchmarks.length ?? 0) === 0,
);
const mode = computed(() => benchmarks.data.value?.mode ?? "off");

// While an administrator's run waits, look again every few seconds.
let timer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(timer));
const waiting = ref(false);
function poll(times = 20) {
  clearTimeout(timer);
  if (times <= 0) {
    waiting.value = false;
    return;
  }
  timer = setTimeout(async () => {
    await client.invalidateQueries({ queryKey: ["entry", props.uuid, "benchmarks"] });
    const due = benchmarks.data.value?.benchmarks.some((b) => b.records.due > 0);
    if (due) poll(times - 1);
    else waiting.value = false;
  }, 3000);
}

const record = ref("");
const running = ref(false);
async function run() {
  running.value = true;
  try {
    const { queued } = await api.runBenchmarks(props.uuid, record.value.trim() || undefined);
    toast(t("benchmarks.queued", { n: queued }, queued));
    waiting.value = true;
    poll();
  } catch (err) {
    toast(err instanceof ApiError ? err.message : String(err), "error");
  } finally {
    running.value = false;
  }
}

/** Per benchmark: which condition's records are listed, and whether met or not. */
interface Filter {
  condition: string;
  met: "false" | "true" | "";
  page: number;
}
const filters = reactive<Record<string, Filter>>({});
const pages = reactive<Record<string, BenchmarkResultPage | null>>({});
const SIZE = 10;

function filterOf(b: EntryBenchmark): Filter {
  if (!filters[b.iri]) {
    const worst = [...b.conditions].sort((x, y) => y.notMet - x.notMet)[0];
    filters[b.iri] = { condition: worst?.id ?? "", met: worst?.notMet ? "false" : "", page: 0 };
  }
  return filters[b.iri]!;
}

async function load(b: EntryBenchmark) {
  const f = filterOf(b);
  try {
    pages[b.iri] = await api.benchmarkResults(props.uuid, {
      benchmark: b.iri,
      condition: f.condition || undefined,
      met: f.met === "" ? undefined : f.met === "true",
      page: f.page,
      size: SIZE,
    });
  } catch {
    pages[b.iri] = null;
  }
}

watch(
  () => benchmarks.data.value,
  (value) => {
    for (const b of value?.benchmarks ?? []) if (b.records.assessed) void load(b);
  },
  { immediate: true },
);

function refilter(b: EntryBenchmark) {
  filterOf(b).page = 0;
  void load(b);
}

function turn(b: EntryBenchmark, delta: number) {
  filterOf(b).page += delta;
  void load(b);
}

const details = reactive<Record<string, BenchmarkResultDetail | "loading">>({});
async function toggle(item: BenchmarkResult) {
  if (details[item.id]) {
    delete details[item.id];
    return;
  }
  details[item.id] = "loading";
  try {
    details[item.id] = await api.benchmarkResult(item.id);
  } catch {
    delete details[item.id];
  }
}

const TONE = { pass: "ok", fail: "bad", indeterminate: "info", error: "warn" } as const;
function tone(result: string | null | undefined) {
  return TONE[(result ?? "") as keyof typeof TONE] ?? "info";
}
function metLabel(met: boolean | null) {
  return met === true
    ? t("benchmarks.met")
    : met === false
      ? t("benchmarks.notMet")
      : t("benchmarks.undecided");
}
function share(met: number, total: number) {
  return total ? Math.round((met / total) * 100) : 0;
}
</script>

<template>
  <section v-if="!hidden && benchmarks.data.value" class="card stack" aria-labelledby="bench-h">
    <h2 id="bench-h">{{ t("benchmarks.title") }}</h2>
    <p class="muted small">{{ t("benchmarks.lead") }}</p>
    <p v-if="mode === 'off'" class="muted small">{{ t("benchmarks.off") }}</p>
    <p v-else-if="mode === 'onRequest'" class="muted small">{{ t("benchmarks.onRequest") }}</p>

    <article v-for="b in benchmarks.data.value.benchmarks" :key="b.iri" class="stack bench">
      <div class="row head">
        <h3>
          <a :href="b.landingPage || b.iri" target="_blank" rel="noopener noreferrer">
            {{ b.title || b.iri }}
          </a>
        </h3>
        <span class="spacer" />
        <span v-if="b.lastAssessedAt" class="muted small" :title="absoluteTime(b.lastAssessedAt)">
          {{ t("benchmarks.lastAssessed", { when: relativeTime(b.lastAssessedAt) }) }}
        </span>
      </div>
      <p v-if="!b.runnable" class="muted small">
        {{ t("benchmarks.notRun", { reason: b.reason ?? "" }) }}
      </p>
      <template v-else>
        <p class="small">
          {{
            b.records.total
              ? t("benchmarks.records", {
                  assessed: number(b.records.assessed),
                  total: number(b.records.total),
                })
              : t("benchmarks.noRecords")
          }}
          <template v-if="b.records.due">
            · {{ t("benchmarks.due", { n: number(b.records.due) }) }}
          </template>
          <template v-if="b.records.failed">
            · {{ t("benchmarks.failed", { n: number(b.records.failed) }) }}
          </template>
        </p>

        <ul v-if="b.conditions.length" class="conditions" :aria-label="t('benchmarks.conditions')">
          <li v-for="c in b.conditions" :key="c.id">
            <div class="row">
              <span class="grow">{{ c.description || c.id }}</span>
              <span class="small">
                {{
                  t("benchmarks.metBy", {
                    met: number(c.met),
                    total: number(c.met + c.notMet + c.undecided),
                  })
                }}
              </span>
            </div>
            <div
              class="bar"
              role="img"
              :aria-label="
                t('benchmarks.share', { n: share(c.met, c.met + c.notMet + c.undecided) })
              "
            >
              <span :style="{ width: `${share(c.met, c.met + c.notMet + c.undecided)}%` }" />
            </div>
          </li>
        </ul>

        <div v-if="b.root" class="stack root">
          <h4>{{ t("benchmarks.root") }}</h4>
          <ul class="verdicts">
            <li v-for="c in b.root.conditions" :key="c.id">
              <span :class="['chip', c.met ? 'ok' : c.met === false ? 'warn' : 'info']">
                {{ metLabel(c.met) }}
              </span>
              <span class="small">{{ c.message || c.description }}</span>
            </li>
          </ul>
        </div>

        <details v-if="b.records.assessed" class="records">
          <summary>{{ t("benchmarks.recordsTitle") }}</summary>
          <div class="row filters">
            <label class="small">
              {{ t("benchmarks.condition") }}
              <select v-model="filterOf(b).condition" class="input sm" @change="refilter(b)">
                <option value="">{{ t("benchmarks.anyCondition") }}</option>
                <option v-for="c in b.conditions" :key="c.id" :value="c.id">
                  {{ c.description || c.id }}
                </option>
              </select>
            </label>
            <label class="small">
              {{ t("benchmarks.show") }}
              <select v-model="filterOf(b).met" class="input sm" @change="refilter(b)">
                <option value="false">{{ t("benchmarks.notMet") }}</option>
                <option value="true">{{ t("benchmarks.met") }}</option>
                <option value="">{{ t("benchmarks.all") }}</option>
              </select>
            </label>
          </div>
          <p v-if="pages[b.iri] && !pages[b.iri]!.total" class="muted small">
            {{ t("benchmarks.noneMatch") }}
          </p>
          <ul v-else-if="pages[b.iri]" class="items">
            <li v-for="item in pages[b.iri]!.items" :key="item.id" class="stack">
              <a :href="item.record" target="_blank" rel="noopener noreferrer" class="mono small">
                {{ item.record }}
              </a>
              <ul class="verdicts">
                <li v-for="c in item.conditions" :key="c.id">
                  <span :class="['chip', c.met ? 'ok' : c.met === false ? 'warn' : 'info']">
                    {{ metLabel(c.met) }}
                  </span>
                  <span class="small">{{ c.description || c.id }}</span>
                  <span v-for="g in c.guidance" :key="g.url" class="small">
                    · <a :href="g.url" target="_blank" rel="noopener noreferrer">{{ g.text }}</a>
                  </span>
                </li>
              </ul>
              <p v-if="item.incomplete" class="small stop">{{ t("benchmarks.incomplete") }}</p>
              <button type="button" class="btn sm ghost" @click="toggle(item)">
                {{ details[item.id] ? t("benchmarks.hideTests") : t("benchmarks.tests") }}
              </button>
              <div v-if="details[item.id] && details[item.id] !== 'loading'" class="stack tests">
                <div
                  v-for="test in (details[item.id] as BenchmarkResultDetail).tests"
                  :key="test.reference ?? test.test ?? ''"
                  class="stack"
                >
                  <div class="row">
                    <span :class="['chip', tone(test.result)]">
                      {{ t(`benchmarks.results.${test.result ?? "unknown"}`) }}
                    </span>
                    <a
                      v-if="test.test"
                      :href="test.test"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="small"
                    >
                      {{ test.title || test.test }}
                    </a>
                  </div>
                  <details v-if="test.log">
                    <summary class="small">{{ t("benchmarks.log") }}</summary>
                    <pre class="log">{{ test.log }}</pre>
                  </details>
                </div>
                <a
                  v-if="(details[item.id] as BenchmarkResultDetail).resultSet"
                  :href="(details[item.id] as BenchmarkResultDetail).resultSet!"
                  class="small"
                  target="_blank"
                  rel="noopener"
                >
                  {{ t("benchmarks.resultSet") }}
                </a>
              </div>
            </li>
          </ul>
          <div v-if="pages[b.iri] && pages[b.iri]!.total > SIZE" class="row">
            <button
              type="button"
              class="btn sm"
              :disabled="filterOf(b).page === 0"
              @click="turn(b, -1)"
            >
              {{ t("benchmarks.previous") }}
            </button>
            <span class="small muted">
              {{
                t("benchmarks.page", {
                  page: filterOf(b).page + 1,
                  pages: Math.ceil(pages[b.iri]!.total / SIZE),
                })
              }}
            </span>
            <button
              type="button"
              class="btn sm"
              :disabled="(filterOf(b).page + 1) * SIZE >= pages[b.iri]!.total"
              @click="turn(b, 1)"
            >
              {{ t("benchmarks.next") }}
            </button>
          </div>
        </details>
      </template>
    </article>

    <form v-if="session.isAdmin && mode !== 'off'" class="run stack" @submit.prevent="run">
      <label for="bench-record" class="label">{{ t("benchmarks.runTitle") }}</label>
      <div class="row">
        <input
          id="bench-record"
          v-model="record"
          class="input grow"
          type="url"
          :placeholder="clientUrl"
        />
        <button class="btn" type="submit" :disabled="running || waiting">
          {{ waiting ? t("benchmarks.waiting") : t("benchmarks.run") }}
        </button>
      </div>
      <span class="help small muted">{{ t("benchmarks.runHelp") }}</span>
    </form>
  </section>
</template>

<style scoped>
.head {
  gap: var(--fair-space-3);
}
.bench + .bench {
  padding-top: var(--fair-space-4);
  border-top: 1px solid var(--fair-separator);
}
.bench h3,
.root h4 {
  margin: 0;
}
.conditions,
.verdicts,
.items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fair-space-2);
}
.verdicts li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--fair-space-2);
}
.grow {
  flex: 1 1 auto;
  min-width: 0;
}
.bar {
  height: 6px;
  border-radius: 3px;
  background: var(--fair-separator);
  overflow: hidden;
}
.bar span {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--tool-accent);
}
.items > li {
  padding: var(--fair-space-3) 0;
  border-top: 1px solid var(--fair-separator);
}
.items a.mono {
  overflow-wrap: anywhere;
}
.filters {
  flex-wrap: wrap;
  gap: var(--fair-space-3);
  margin: var(--fair-space-2) 0;
}
.filters label {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
}
.log {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.8rem;
}
.tests {
  padding-left: var(--fair-space-3);
  border-left: 2px solid var(--fair-separator);
}
.run .row {
  gap: var(--fair-space-3);
  flex-wrap: nowrap;
}
.run .input {
  flex: 1 1 auto;
  min-width: 0;
}
.items .btn {
  justify-self: start;
}
.stop {
  color: var(--fair-warning);
  font-weight: var(--fair-weight-semibold);
}
</style>
