<script setup lang="ts">
/**
 * An FDP's harvests, the latest first (TASKS 15.3): when the index walked its
 * metadata, how the walk ended and what it read, each linked to its page and
 * its PROV-O description.
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { api } from "@/api";
import { currentLocale } from "@/i18n";
import { absoluteTime, relativeTime } from "@/utils/format";
import { outcomeKey, outcomeTone, span } from "./outcome";

const props = defineProps<{ uuid: string }>();
const { t } = useI18n();
const number = (n: number) => new Intl.NumberFormat(currentLocale()).format(n);

const page = ref(0);
const SIZE = 5;
const harvests = useQuery({
  queryKey: computed(() => ["entry", props.uuid, "harvests", page.value]),
  queryFn: () => api.entryHarvests(props.uuid, page.value, SIZE),
  placeholderData: keepPreviousData,
  retry: false,
});
const items = computed(() => harvests.data.value?.items ?? []);
const total = computed(() => harvests.data.value?.total ?? 0);
</script>

<template>
  <div v-if="total" class="stack">
    <h3>{{ t("harvests.title") }}</h3>
    <p class="muted small">{{ t("harvests.lead") }}</p>
    <ul class="harvests">
      <li v-for="h in items" :key="h.id" class="row">
        <span :class="['chip', outcomeTone(h.outcome)]">
          {{ t(`harvests.outcomes.${outcomeKey(h.outcome)}`) }}
        </span>
        <router-link
          :to="{ name: 'harvest', params: { id: h.id } }"
          :title="absoluteTime(h.startedAt)"
        >
          {{ relativeTime(h.startedAt) }}
        </router-link>
        <span class="muted small">
          {{
            t("harvests.summary", {
              read: number(h.pagesRead),
              changed: number(h.added + h.changed + h.removed),
              failed: number(h.failed),
            })
          }}
          <template v-if="h.endedAt"> · {{ span(h.startedAt, h.endedAt) }}</template>
          <template v-if="h.runs.length > 1">
            · {{ t("harvests.runCount", { n: h.runs.length }, h.runs.length) }}
          </template>
        </span>
      </li>
    </ul>
    <div v-if="total > SIZE" class="row">
      <button type="button" class="btn sm" :disabled="page === 0" @click="page--">
        {{ t("harvests.newer") }}
      </button>
      <button type="button" class="btn sm" :disabled="(page + 1) * SIZE >= total" @click="page++">
        {{ t("harvests.older") }}
      </button>
    </div>
  </div>
</template>

<style scoped>
h3 {
  margin: 0;
}
.harvests {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fair-space-2);
}
.harvests li {
  flex-wrap: wrap;
  gap: var(--fair-space-2);
}
</style>
