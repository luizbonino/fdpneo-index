<script setup lang="ts">
/**
 * An FDP's metadata health (TASKS 15.8): how far its metadata reaches, what
 * the index found with how to fix it, the pages it could not read, and where
 * the index stopped by its own rules. Hidden when reports are for
 * administrators and the reader is not one.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { api, HEALTH_LEVELS, type HealthRequirement } from "@/api";
import { shortIri } from "@/sparql/results";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { absoluteTime, relativeTime } from "@/utils/format";

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
const reachIndex = computed(() =>
  report.value?.reach ? HEALTH_LEVELS.indexOf(report.value.reach as never) : -1,
);
const TONE = { fine: "ok", findings: "warn", unreadable: "bad" } as const;
const number = (n: number) => new Intl.NumberFormat().format(n);
/** A network profile's requirement as a sentence (TASKS 15.9). */
function requirement(item: HealthRequirement): string {
  const level = item.level ? t(`health.levels.${item.level}`) : "";
  if (item.code === "reach") return t("health.requirement.reach", { level });
  const property = item.property ? shortIri(item.property) : "";
  return t(`health.requirement.${item.code}`, { level, property, n: item.count });
}
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
    <template v-else>
      <p class="muted small">{{ t("health.lead") }}</p>
      <ol class="reach" :aria-label="t('health.reachLabel')">
        <li
          v-for="(level, i) in HEALTH_LEVELS"
          :key="level"
          :class="{ reached: i <= reachIndex, last: i === reachIndex }"
        >
          <span class="level">{{ t(`health.levels.${level}`) }}</span>
          <span class="count">{{ number(report.levels[level] ?? 0) }}</span>
        </li>
      </ol>
      <p class="small">
        {{
          report.reach
            ? t("health.reaches", { level: t(`health.levels.${report.reach}`) })
            : t("health.reachNone")
        }}
        <template v-if="report.levels.other">
          · {{ t("health.otherPages", { n: number(report.levels.other) }) }}
        </template>
      </p>

      <p v-if="report.stops.atCeiling" class="stop small">
        {{ t("health.stops.ceiling", { n: number(report.stops.unread) }) }}
      </p>
      <p v-if="report.stops.offSite" class="muted small">
        {{ t("health.stops.offSite", { n: number(report.stops.offSite) }) }}
      </p>

      <ul v-if="report.findings.length" class="findings">
        <li v-for="finding in report.findings" :key="finding.code">
          <strong>
            {{ t(`health.findings.${finding.code}.title`) }}
            <span class="chip warn">{{ number(finding.count) }}</span>
          </strong>
          <span class="small">{{ t(`health.findings.${finding.code}.fix`) }}</span>
          <details>
            <summary class="small">{{ t("health.examples") }}</summary>
            <ul class="examples small">
              <li v-for="iri in finding.examples" :key="iri">
                <a :href="iri" target="_blank" rel="noopener noreferrer" class="mono">{{ iri }}</a>
              </li>
            </ul>
          </details>
        </li>
      </ul>

      <template v-if="report.requirements?.length">
        <h3>{{ t("health.requirementsTitle") }}</h3>
        <p class="muted small">
          {{ t("health.requirementsLead") }}
          <router-link :to="{ name: 'expectations' }">{{ t("expectations.title") }}</router-link>
        </p>
        <ul class="findings">
          <li v-for="(item, i) in report.requirements" :key="`r${i}`">
            <strong>
              {{ requirement(item) }}
              <span v-if="item.code !== 'reach'" class="chip warn">{{ number(item.count) }}</span>
            </strong>
            <details v-if="item.examples.length">
              <summary class="small">{{ t("health.examples") }}</summary>
              <ul class="examples small">
                <li v-for="iri in item.examples" :key="iri">
                  <a :href="iri" target="_blank" rel="noopener noreferrer" class="mono">{{
                    iri
                  }}</a>
                </li>
              </ul>
            </details>
          </li>
        </ul>
      </template>
      <template v-if="report.recommendations?.length">
        <h3>{{ t("health.recommendationsTitle") }}</h3>
        <ul class="findings">
          <li v-for="(item, i) in report.recommendations" :key="`c${i}`">
            <strong>
              {{ requirement(item) }}
              <span class="chip info">{{ number(item.count) }}</span>
            </strong>
          </li>
        </ul>
      </template>

      <template v-if="report.notes?.length">
        <h3>{{ t("health.notesTitle") }}</h3>
        <p class="muted small">{{ t("health.notesLead") }}</p>
        <ul class="findings">
          <li v-for="note in report.notes" :key="note.code">
            <strong>
              {{ t(`health.findings.${note.code}.title`) }}
              <span class="chip info">{{ number(note.count) }}</span>
            </strong>
            <span class="small">{{ t(`health.findings.${note.code}.fix`) }}</span>
            <details>
              <summary class="small">{{ t("health.examples") }}</summary>
              <ul class="examples small">
                <li v-for="iri in note.examples" :key="iri">
                  <a :href="iri" target="_blank" rel="noopener noreferrer" class="mono">{{
                    iri
                  }}</a>
                </li>
              </ul>
            </details>
          </li>
        </ul>
      </template>

      <template v-if="report.failures.length">
        <h3>{{ t("health.failuresTitle") }}</h3>
        <ul class="findings">
          <li v-for="failure in report.failures" :key="failure.kind">
            <strong>
              {{ t(`health.failures.${failure.kind}`) }}
              <span class="chip bad">{{ number(failure.count) }}</span>
            </strong>
            <details>
              <summary class="small">{{ t("health.examples") }}</summary>
              <ul class="examples small">
                <li v-for="iri in failure.examples" :key="iri">
                  <a :href="iri" target="_blank" rel="noopener noreferrer" class="mono">{{
                    iri
                  }}</a>
                </li>
              </ul>
            </details>
          </li>
        </ul>
      </template>
      <p v-if="report.complete === false && !report.stops.atCeiling" class="muted small">
        {{ t("health.incomplete") }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.head {
  gap: var(--fair-space-3);
}
.reach {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.reach li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--fair-space-2) var(--fair-space-3);
  background: var(--fair-bg);
  color: var(--fair-text-muted);
}
.reach li:first-child {
  border-radius: 4px 0 0 4px;
}
.reach li:last-child {
  border-radius: 0 4px 4px 0;
}
.reach li.reached {
  background: var(--tool-accent);
  color: var(--fair-text-on-accent);
}
.reach .level {
  font-size: var(--fair-text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.reach .count {
  font-weight: var(--fair-weight-semibold);
  font-variant-numeric: tabular-nums;
}
.stop {
  color: var(--fair-warning);
  font-weight: var(--fair-weight-semibold);
}
.findings {
  display: grid;
  gap: var(--fair-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}
.findings > li {
  display: grid;
  gap: var(--fair-space-1);
  padding-top: var(--fair-space-3);
  border-top: 1px solid var(--fair-separator);
}
.findings strong {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
}
.examples {
  margin: var(--fair-space-1) 0 0;
  padding-left: var(--fair-space-5);
}
.examples a {
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .reach {
    grid-template-columns: 1fr;
  }
  .reach li,
  .reach li:first-child,
  .reach li:last-child {
    flex-direction: row;
    justify-content: space-between;
    border-radius: 0;
  }
}
</style>
