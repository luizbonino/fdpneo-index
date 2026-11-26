<script setup lang="ts">
/**
 * One harvest (TASKS 15.3): a walk of the index over an FDP's metadata, how it
 * ended, what it read, added, changed, removed and could not read, and its
 * runs. The page of the harvest's IRI, `/index/harvests/{id}`, which a browser
 * is sent to; the same in PROV-O as Turtle or JSON-LD.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api";
import { ApiError } from "@/api/client";
import CopyField from "@/components/CopyField.vue";
import { outcomeKey, outcomeTone, span } from "@/components/harvest/outcome";
import { currentLocale } from "@/i18n";
import { absoluteTime } from "@/utils/format";

const props = defineProps<{ id: string }>();
const { t } = useI18n();
const number = (n: number) => new Intl.NumberFormat(currentLocale()).format(n);

const harvest = useQuery({
  queryKey: computed(() => ["harvest", props.id]),
  queryFn: () => api.harvest(props.id),
  retry: false,
});
const h = computed(() => harvest.data.value);
const notFound = computed(
  () =>
    harvest.error.value instanceof ApiError && [400, 404, 422].includes(harvest.error.value.status),
);
const COUNTS = ["pagesRead", "added", "changed", "removed", "notModified", "failed"] as const;
</script>

<template>
  <div class="page narrow">
    <p v-if="harvest.isLoading.value" class="muted">{{ t("common.loading") }}</p>
    <section v-else-if="notFound" class="card empty">
      <h1>{{ t("harvests.notFound") }}</h1>
      <router-link class="btn" :to="{ name: 'directory' }">{{ t("common.home") }}</router-link>
    </section>
    <div v-else-if="harvest.isError.value" class="notice error">
      {{ (harvest.error.value as Error).message }}
    </div>

    <template v-else-if="h">
      <nav class="crumbs" :aria-label="t('resource.path')">
        <router-link :to="{ name: 'directory' }">{{ t("nav.directory") }}</router-link>
        <span aria-hidden="true">›</span>
        <router-link :to="{ name: 'entry', params: { uuid: h.entry } }">
          {{ h.title || h.clientUrl }}
        </router-link>
      </nav>
      <header class="page-head">
        <div>
          <p class="eyebrow">{{ t("harvests.eyebrow") }}</p>
          <h1>{{ t("harvests.heading", { when: absoluteTime(h.startedAt) }) }}</h1>
          <p class="lead">{{ t("harvests.pageLead") }}</p>
        </div>
        <span :class="['chip', outcomeTone(h.outcome)]">
          {{ t(`harvests.outcomes.${outcomeKey(h.outcome)}`) }}
        </span>
      </header>

      <section class="card stack">
        <dl class="facts">
          <dt>{{ t("harvests.fdp") }}</dt>
          <dd>
            <a :href="h.clientUrl" target="_blank" rel="noopener noreferrer">{{ h.clientUrl }}</a>
          </dd>
          <dt>{{ t("harvests.started") }}</dt>
          <dd>{{ absoluteTime(h.startedAt) }}</dd>
          <template v-if="h.endedAt">
            <dt>{{ t("harvests.ended") }}</dt>
            <dd>{{ absoluteTime(h.endedAt) }} · {{ span(h.startedAt, h.endedAt) }}</dd>
          </template>
          <template v-for="key in COUNTS" :key="key">
            <dt>{{ t(`harvests.counts.${key}`) }}</dt>
            <dd>{{ number(h[key]) }}</dd>
          </template>
          <template v-if="h.failureReason">
            <dt>{{ t("harvests.failureReason") }}</dt>
            <dd>{{ h.failureReason }}</dd>
          </template>
        </dl>
        <p v-if="h.atCeiling" class="small stop">{{ t("harvests.atCeiling") }}</p>
        <p class="muted small">{{ t(`harvests.explain.${outcomeKey(h.outcome)}`) }}</p>
      </section>

      <section v-if="h.runs.length" class="card stack" aria-labelledby="runs-h">
        <h2 id="runs-h">{{ t("harvests.runsTitle", { n: h.runs.length }, h.runs.length) }}</h2>
        <p class="muted small">{{ t("harvests.runsLead") }}</p>
        <div class="table-scroll">
          <table class="table">
            <thead>
              <tr>
                <th scope="col">{{ t("harvests.run") }}</th>
                <th scope="col">{{ t("harvests.started") }}</th>
                <th v-for="key in COUNTS" :key="key" scope="col" class="num">
                  {{ t(`harvests.counts.${key}`) }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in h.runs" :key="run.run">
                <td>{{ run.run }}</td>
                <td :title="absoluteTime(run.startedAt)">
                  {{ absoluteTime(run.startedAt) }}
                  <span v-if="run.startedAt && run.endedAt" class="muted small">
                    · {{ span(run.startedAt, run.endedAt) }}
                  </span>
                </td>
                <td v-for="key in COUNTS" :key="key" class="num">{{ number(run[key]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card stack" aria-labelledby="prov-h">
        <h2 id="prov-h">{{ t("harvests.provTitle") }}</h2>
        <p class="muted small">{{ t("harvests.provLead") }}</p>
        <CopyField :value="h.iri" :label="t('harvests.iri')" />
        <div class="row">
          <a class="btn sm" :href="api.harvestRdf(h.id, 'ttl')" target="_blank" rel="noopener">
            Turtle
          </a>
          <a class="btn sm" :href="api.harvestRdf(h.id, 'jsonld')" target="_blank" rel="noopener">
            JSON-LD
          </a>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  align-items: flex-start;
}
.table-scroll {
  overflow-x: auto;
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.stop {
  color: var(--fair-warning);
  font-weight: var(--fair-weight-semibold);
}
.row {
  gap: var(--fair-space-3);
}
</style>
