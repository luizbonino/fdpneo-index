<script setup lang="ts">
/**
 * An FDP's metadata health (TASKS 15.8), from its stored pages: the report
 * (``HealthReport``) under a heading with its status and when it was
 * computed. Hidden when reports are for administrators and the reader is not
 * one.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { absoluteTime, relativeTime } from "@/utils/format";
import HealthReport from "./HealthReport.vue";

const props = defineProps<{ uuid: string }>();
const { t } = useI18n();

const health = useQuery({
  queryKey: computed(() => ["entry", props.uuid, "health", session.isAdmin]),
  queryFn: () => api.entryHealth(props.uuid),
  retry: false,
});
const hidden = computed(
  () =>
    health.error.value instanceof ApiError && [401, 403, 404].includes(health.error.value.status),
);
const report = computed(() => health.data.value ?? null);
const TONE = { fine: "ok", findings: "warn", unreadable: "bad" } as const;
</script>

<template>
  <section v-if="!hidden && !health.isPending.value" class="card stack" aria-labelledby="health-h">
    <div class="row head">
      <h2 id="health-h">{{ t("health.title") }}</h2>
      <span v-if="report" :class="['chip', TONE[report.status]]">
        {{ t(`health.status.${report.status}`) }}
      </span>
      <span class="spacer" />
      <span v-if="report" class="muted small" :title="absoluteTime(report.computedAt)">
        {{ t("health.checked", { when: relativeTime(report.computedAt) }) }}
      </span>
    </div>
    <p v-if="!report" class="muted">{{ t("health.none") }}</p>
    <HealthReport v-else :report="report" />
  </section>
</template>

<style scoped>
.head {
  gap: var(--fair-space-3);
}
</style>
