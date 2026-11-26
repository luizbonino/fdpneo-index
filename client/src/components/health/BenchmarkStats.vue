<script setup lang="ts">
/**
 * The network's benchmarks over the FDPs in the public list (TASKS 15.10): per
 * condition, the FDPs whose assessed records all meet it, and the records that
 * meet it out of those assessed. The network's own verdict, not the index's.
 */
import { useI18n } from "vue-i18n";
import type { NetworkBenchmark } from "@/api";
import { currentLocale } from "@/i18n";

defineProps<{ benchmarks: NetworkBenchmark[] }>();
const { t } = useI18n();
const number = (n: number) => new Intl.NumberFormat(currentLocale()).format(n);
const share = (n: number, total: number) => (total ? Math.round((n / total) * 100) : 0);
</script>

<template>
  <section class="card wide stack" aria-labelledby="h-benchmarks">
    <h2 id="h-benchmarks">{{ t("stats.benchmarks") }}</h2>
    <p class="muted small">{{ t("stats.benchmarksLead") }}</p>
    <div v-for="b in benchmarks" :key="b.iri" class="stack">
      <h3>
        <a :href="b.landingPage || b.iri" target="_blank" rel="noopener noreferrer">
          {{ b.title || b.iri }}
        </a>
      </h3>
      <p v-if="!b.runnable" class="muted small">
        {{ t("benchmarks.notRun", { reason: b.reason ?? "" }) }}
      </p>
      <p v-else-if="!b.fdps" class="muted small">{{ t("stats.benchmarksNone") }}</p>
      <div v-else class="table-wrap">
        <table class="table">
          <caption class="small muted">
            {{
              t("stats.benchmarksFdps", { n: number(b.fdps) })
            }}
          </caption>
          <thead>
            <tr>
              <th scope="col">{{ t("benchmarks.condition") }}</th>
              <th scope="col" class="num">{{ t("stats.benchmarksAllMet") }}</th>
              <th scope="col" class="num">{{ t("stats.benchmarksRecordsMet") }}</th>
              <th scope="col">{{ t("stats.benchmarksShare") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in b.conditions" :key="c.id">
              <td>{{ c.description || c.id }}</td>
              <td class="num">{{ number(c.fdps) }} / {{ number(b.fdps) }}</td>
              <td class="num">{{ number(c.records) }} / {{ number(c.assessed) }}</td>
              <td>
                <div
                  class="bar"
                  role="img"
                  :aria-label="t('benchmarks.share', { n: share(c.records, c.assessed) })"
                >
                  <span :style="{ width: `${share(c.records, c.assessed)}%` }" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
h3 {
  margin: 0;
}
.table-wrap {
  overflow-x: auto;
}
caption {
  text-align: left;
  caption-side: top;
}
.bar {
  min-width: 80px;
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
</style>
