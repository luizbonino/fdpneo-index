<script setup lang="ts">
/**
 * An FDP's conformance report (TASKS 11.3): per profile, its records by
 * status, and the most frequent problems with the records that have them.
 * Hidden when the index does not validate, or reports are for administrators
 * and the reader is not one.
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { toast } from "@/composables/feedback";
import { useServiceInfo } from "@/composables/queries";
import {
  componentName,
  conformanceApi,
  packTitle,
  SEVERITY_TONE,
  type Problem,
} from "@/conformance/api";
import { shortIri } from "@/sparql/results";
import { relativeTime, absoluteTime } from "@/utils/format";

const props = defineProps<{ uuid: string }>();
const { t, locale } = useI18n();
const info = useServiceInfo();

const report = useQuery({
  queryKey: computed(() => ["conformance-entry", props.uuid, session.isAdmin]),
  queryFn: () => conformanceApi.entry(props.uuid),
  retry: false,
});
const packs = useQuery({ queryKey: ["conformance-packs"], queryFn: conformanceApi.packs });

const hidden = computed(
  () =>
    report.error.value instanceof ApiError && [401, 403, 404].includes(report.error.value.status),
);
const validated = computed(() => (report.data.value?.packs ?? []).some((p) => p.records > 0));

// Lenses (TASKS 15.13): profiles visitors may look through, one at a time, worded as not
// claimed. What records declare and what the index requires are always shown.
const lens = ref("");
const lenses = computed(() => (report.data.value?.packs ?? []).filter((p) => p.role === "lens"));
const shown = computed(() =>
  (report.data.value?.packs ?? []).filter((p) => p.role !== "lens" || p.pack === lens.value),
);
const problems = computed(() => {
  const visible = new Set(shown.value.map((p) => p.pack));
  return (report.data.value?.problems ?? []).filter((p) => visible.has(p.pack));
});
function basis(pack: { pack: string; role: string; claimed: number }): string {
  if (pack.role === "declared") return t("conformance.declared.basis");
  if (pack.role === "required") return t("conformance.declared.required");
  return pack.claimed
    ? t("conformance.lens.claimed", { n: pack.claimed }, pack.claimed)
    : t("conformance.lens.notClaimed");
}

const open = ref<string | null>(null);
function problemKey(p: Problem): string {
  return [p.pack, p.severity, p.path ?? "", p.component, p.shape ?? ""].join("|");
}
const openProblem = computed(() =>
  report.data.value?.problems.find((p) => problemKey(p) === open.value),
);
const records = useQuery({
  queryKey: computed(() => ["conformance-records", props.uuid, open.value]),
  queryFn: () => {
    const p = openProblem.value!;
    return conformanceApi.records({
      entry: props.uuid,
      pack: p.pack,
      path: p.path,
      component: p.component,
      severity: p.severity,
      size: 20,
    });
  },
  enabled: computed(() => openProblem.value !== undefined),
});

function toggle(p: Problem) {
  const key = problemKey(p);
  open.value = open.value === key ? null : key;
}

async function validateAgain() {
  try {
    await conformanceApi.validate(props.uuid);
    toast(t("conformance.validating"));
  } catch (err) {
    toast((err as Error).message, "error");
  }
}

function title(id: string): string {
  return packTitle(packs.data.value, id, locale.value);
}
</script>

<template>
  <section v-if="!hidden && report.data.value" class="card stack" aria-labelledby="conformance-h">
    <div class="row head">
      <h2 id="conformance-h">{{ t("conformance.heading") }}</h2>
      <span class="spacer" />
      <button v-if="session.isAdmin" type="button" class="btn sm" @click="validateAgain">
        {{ t("conformance.validateAgain") }}
      </button>
    </div>
    <p class="muted small">{{ t("conformance.lead") }}</p>
    <p v-if="!validated" class="muted">{{ t("conformance.notYet") }}</p>
    <template v-else>
      <label v-if="lenses.length" class="row lens small">
        <span>{{ t("conformance.lens.choose") }}</span>
        <select v-model="lens" class="input select">
          <option value="">{{ t("conformance.lens.none") }}</option>
          <option v-for="p in lenses" :key="p.pack" :value="p.pack">{{ title(p.pack) }}</option>
        </select>
      </label>
      <div class="table-scroll">
        <table class="table">
          <caption class="visually-hidden">
            {{
              t("conformance.summaryCaption")
            }}
          </caption>
          <thead>
            <tr>
              <th scope="col">{{ t("conformance.profile") }}</th>
              <th scope="col" class="num">{{ t("conformance.records") }}</th>
              <th scope="col" class="num">{{ t("conformance.conform") }}</th>
              <th scope="col" class="num">{{ t("conformance.withViolations") }}</th>
              <th scope="col" class="num">{{ t("conformance.withWarnings") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="pack in shown" :key="pack.pack">
              <th scope="row">
                {{ title(pack.pack) }}
                <span class="muted small block">{{ basis(pack) }}</span>
                <span v-if="pack.undeclared || pack.unresolved" class="muted small block">
                  <template v-if="pack.undeclared">
                    {{
                      t("conformance.declared.undeclared", { n: pack.undeclared }, pack.undeclared)
                    }}
                  </template>
                  <template v-if="pack.undeclared && pack.unresolved"> · </template>
                  <template v-if="pack.unresolved">
                    {{
                      t("conformance.declared.unresolved", { n: pack.unresolved }, pack.unresolved)
                    }}
                  </template>
                </span>
                <span
                  v-if="pack.lastValidated"
                  class="muted small when"
                  :title="absoluteTime(pack.lastValidated)"
                >
                  {{ t("conformance.checked", { when: relativeTime(pack.lastValidated) }) }}
                </span>
              </th>
              <td class="num">{{ pack.records }}</td>
              <td class="num">
                <span :class="['chip', pack.conforms ? 'ok' : 'neutral']">{{ pack.conforms }}</span>
              </td>
              <td class="num">
                <span :class="['chip', pack.violations ? 'bad' : 'neutral']">
                  {{ pack.violations }}
                </span>
                <span v-if="pack.errors" class="muted small">
                  {{ t("conformance.errors", { n: pack.errors }, pack.errors) }}
                </span>
              </td>
              <td class="num">{{ pack.withWarnings }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3 v-if="problems.length">{{ t("conformance.problems") }}</h3>
      <ul class="problems">
        <li v-for="problem in problems" :key="problemKey(problem)">
          <div class="problem">
            <span :class="['chip', SEVERITY_TONE[problem.severity]]">
              {{ t(`conformance.severity.${problem.severity}`) }}
            </span>
            <div class="what">
              <strong>
                <code v-if="problem.path" :title="problem.path">{{ shortIri(problem.path) }}</code>
                {{
                  t(
                    `conformance.component.${componentName(problem.component)}`,
                    componentName(problem.component),
                  )
                }}
              </strong>
              <span class="muted small">
                {{ title(problem.pack) }} ·
                {{ t("conformance.inRecords", { n: problem.records }, problem.records) }}
              </span>
              <span v-if="problem.message" class="small message">{{ problem.message }}</span>
            </div>
            <button
              type="button"
              class="btn ghost sm"
              :aria-expanded="open === problemKey(problem)"
              @click="toggle(problem)"
            >
              {{
                open === problemKey(problem)
                  ? t("conformance.hideRecords")
                  : t("conformance.showRecords")
              }}
            </button>
          </div>
          <ul v-if="open === problemKey(problem)" class="records">
            <li v-if="records.isPending.value" class="muted">{{ t("common.loading") }}</li>
            <li v-for="item in records.data.value?.items ?? []" :key="item.publicId">
              <router-link
                v-if="info.data.value?.search"
                :to="{ name: 'resource', params: { id: item.publicId }, hash: '#conformance' }"
              >
                {{ item.title || shortIri(item.iri) }}
              </router-link>
              <span v-else :title="item.iri">{{ shortIri(item.iri) }}</span>
              <span class="muted small">
                · {{ t("conformance.counts", { v: item.violations, w: item.warnings }) }}
              </span>
            </li>
            <li
              v-if="(records.data.value?.total ?? 0) > (records.data.value?.items.length ?? 0)"
              class="muted small"
            >
              {{
                t("conformance.more", {
                  n: records.data.value!.total - records.data.value!.items.length,
                })
              }}
            </li>
          </ul>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
h2,
h3 {
  margin: 0;
}
.head {
  align-items: center;
}
.spacer {
  flex: 1;
}
.table-scroll {
  overflow-x: auto;
}
.table {
  width: 100%;
}
.table tbody th {
  text-transform: none;
  letter-spacing: normal;
  font-family: inherit;
  font-size: inherit;
  font-weight: var(--fair-weight-semibold);
  color: var(--fair-text-strong);
  background: none;
}
.num {
  text-align: right;
  white-space: nowrap;
}
.when {
  display: block;
  font-weight: normal;
}
.problems,
.records {
  list-style: none;
  margin: 0;
  padding: 0;
}
.problems > li {
  padding: var(--fair-space-3) 0;
  border-top: 1px solid var(--fair-separator);
}
.problem {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: var(--fair-space-3);
  align-items: start;
}
.what {
  display: grid;
  gap: 2px;
}
.message {
  overflow-wrap: anywhere;
}
.records a {
  text-decoration: underline;
}
.records {
  margin: var(--fair-space-2) 0 0 var(--fair-space-6);
  display: grid;
  gap: 4px;
}
@media (max-width: 600px) {
  .problem {
    grid-template-columns: 1fr;
  }
}
.block {
  display: block;
}
.lens {
  gap: var(--fair-space-2);
}
.lens select {
  width: auto;
  max-width: 20rem;
}
</style>
