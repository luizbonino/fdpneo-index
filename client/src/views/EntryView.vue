<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { api, type Permit } from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { keys, useEntry, useInvalidateEntries, useServiceInfo } from "@/composables/queries";
import { confirm, toast } from "@/composables/feedback";
import StateBadge from "@/components/StateBadge.vue";
import PermitBadge from "@/components/PermitBadge.vue";
import StatusHelp from "@/components/StatusHelp.vue";
import EventTimeline from "@/components/EventTimeline.vue";
import ConformanceReport from "@/components/conformance/ConformanceReport.vue";
import { absoluteTime, countryLabel, hostOf, relativeTime } from "@/utils/format";
import { currentLocale } from "@/i18n";
import { NUMERIC_TO_ALPHA2 } from "@/components/stats/countryCodes";

const props = defineProps<{ uuid: string }>();
const { t } = useI18n();
const router = useRouter();
const invalidate = useInvalidateEntries();

const entry = useEntry(() => props.uuid);
const info = useServiceInfo();
const detail = computed(() => entry.data.value);
const metadata = computed(() => detail.value?.currentMetadata?.metadata ?? {});
const title = computed(
  () => metadata.value.title || (detail.value ? hostOf(detail.value.clientUrl) : ""),
);
const notFound = computed(
  () => entry.error.value instanceof ApiError && entry.error.value.status === 404,
);

const showPreview = ref(false);
const preview = useQuery({
  queryKey: computed(() => keys.data(props.uuid)),
  queryFn: () => api.harvestedData(props.uuid),
  enabled: showPreview,
});

const FORMATS = [
  { key: "ttl", label: "Turtle", ext: "ttl", type: "text/turtle" },
  { key: "jsonld", label: "JSON-LD", ext: "jsonld", type: "application/ld+json" },
  { key: "nt", label: "N-Triples", ext: "nt", type: "application/n-triples" },
];

async function download(format: (typeof FORMATS)[number]) {
  try {
    const text = await api.harvestedData(props.uuid, format.key);
    const url = URL.createObjectURL(new Blob([text], { type: format.type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${hostOf(detail.value?.clientUrl ?? "fdp")}.${format.ext}`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast((err as Error).message, "error");
  }
}

const busy = ref(false);
async function run(action: () => Promise<unknown>, message: string) {
  busy.value = true;
  try {
    await action();
    toast(message);
    await invalidate(props.uuid);
    setTimeout(() => void invalidate(props.uuid), 2500);
  } catch (err) {
    toast((err as Error).message, "error");
  } finally {
    busy.value = false;
  }
}

function setPermit(permit: Permit) {
  void run(
    () => api.setPermit(props.uuid, permit),
    permit === "ACCEPTED" ? t("entry.accepted") : t("entry.rejected"),
  );
}

/** Per-FDP options (administrators): directory only, and an own harvest interval. */
const HARVEST_INTERVALS = ["", "PT12H", "PT24H", "PT168H"];
function setHarvest(harvest: boolean) {
  void run(
    () => api.setOptions(props.uuid, { harvest }),
    harvest ? t("entry.options.harvestOn") : t("entry.options.harvestOff"),
  );
}
function setHarvestInterval(harvestInterval: string) {
  void run(() => api.setOptions(props.uuid, { harvestInterval }), t("entry.options.saved"));
}

/** Where the FDP is (TASKS 15.4): its country by name, and which source placed it. */
const regions = computed(() => {
  try {
    return new Intl.DisplayNames([currentLocale()], { type: "region" });
  } catch {
    return null;
  }
});
const countryChoices = computed(() =>
  Object.values(NUMERIC_TO_ALPHA2)
    .map((code) => ({ code, name: regions.value?.of(code) ?? code }))
    .sort((a, b) => a.name.localeCompare(b.name, currentLocale())),
);
const location = computed(() => {
  const found = detail.value?.location;
  if (!found) return null;
  return {
    country: regions.value?.of(found.country) ?? found.country,
    source: t(`stats.sources.${found.source}`),
  };
});
function setCountry(country: string) {
  void run(() => api.setOptions(props.uuid, { country }), t("entry.options.saved"));
}

function checkNow() {
  const url = detail.value?.clientUrl ?? "";
  void run(() => api.trigger(url), t("entry.checkStarted", { url }));
}

async function remove() {
  const url = detail.value?.clientUrl ?? "";
  if (
    !(await confirm(t("entry.deleteConfirm", { url }), {
      confirmLabel: t("entry.delete"),
      danger: true,
    }))
  )
    return;
  try {
    await api.deleteEntry(props.uuid);
    await invalidate();
    toast(t("entry.deleted"));
    void router.push({ name: "directory" });
  } catch (err) {
    toast((err as Error).message, "error");
  }
}
</script>

<template>
  <div class="page">
    <router-link class="back small" :to="{ name: 'directory' }"
      >← {{ t("entry.back") }}</router-link
    >

    <p v-if="entry.isPending.value" class="muted">{{ t("common.loading") }}</p>
    <div v-else-if="notFound" class="card stack">
      <h1>{{ t("entry.notFound") }}</h1>
      <router-link class="btn" :to="{ name: 'directory' }">{{ t("common.home") }}</router-link>
    </div>
    <div v-else-if="entry.isError.value" class="notice error">
      {{ (entry.error.value as Error).message }}
    </div>

    <template v-else-if="detail">
      <header class="page-head">
        <div class="stack head">
          <h1>{{ title }}</h1>
          <a class="mono small" :href="detail.clientUrl" target="_blank" rel="noopener noreferrer">
            {{ detail.clientUrl }} ↗
          </a>
          <div class="row">
            <StateBadge :state="detail.state" />
            <PermitBadge v-if="session.isAdmin" :permit="detail.permit" />
            <span
              v-if="detail.expiredTime"
              class="chip warn"
              :title="absoluteTime(detail.expiredTime)"
            >
              {{ t("entry.expired") }}
            </span>
            <span v-if="detail.options && !detail.options.harvest" class="chip info">
              {{ t("entry.options.directoryOnly") }}
            </span>
            <span class="muted small">{{ t(`state.help.${detail.state}`) }}</span>
            <StatusHelp :permits="session.isAdmin" />
          </div>
          <p v-if="location" class="muted small">
            {{ t("entry.location", location) }}
          </p>
        </div>
      </header>

      <section v-if="session.isAdmin" class="card admin" :aria-label="t('entry.administration')">
        <span class="eyebrow">{{ t("entry.administration") }}</span>
        <div class="row">
          <button
            v-if="detail.permit !== 'ACCEPTED'"
            class="btn primary"
            :disabled="busy"
            @click="setPermit('ACCEPTED')"
          >
            {{ t("entry.accept") }}
          </button>
          <button
            v-if="detail.permit !== 'REJECTED'"
            class="btn"
            :disabled="busy"
            @click="setPermit('REJECTED')"
          >
            {{ t("entry.reject") }}
          </button>
          <button class="btn" :disabled="busy" @click="checkNow">{{ t("entry.checkNow") }}</button>
          <button class="btn danger" :disabled="busy" @click="remove">
            {{ t("entry.delete") }}
          </button>
        </div>
        <div v-if="detail.options" class="row options">
          <label class="check">
            <input
              type="checkbox"
              :checked="detail.options.harvest"
              :disabled="busy"
              @change="setHarvest(($event.target as HTMLInputElement).checked)"
            />
            {{ t("entry.options.harvest") }}
          </label>
          <label class="row">
            <span class="small">{{ t("entry.options.interval") }}</span>
            <select
              class="input select"
              :value="detail.options.harvestInterval ?? ''"
              :disabled="busy || !detail.options.harvest"
              @change="setHarvestInterval(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="value in HARVEST_INTERVALS" :key="value" :value="value">
                {{ t(`entry.options.intervals.${value || "changed"}`) }}
              </option>
            </select>
          </label>
          <label class="row">
            <span class="small">{{ t("entry.options.country") }}</span>
            <select
              class="input select"
              :value="detail.options.country ?? ''"
              :disabled="busy"
              @change="setCountry(($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t("entry.options.countryAuto") }}</option>
              <option v-for="c in countryChoices" :key="c.code" :value="c.code">
                {{ c.name }}
              </option>
            </select>
          </label>
        </div>
      </section>

      <div class="grid">
        <section class="card">
          <h2>{{ t("entry.metadata") }}</h2>
          <p v-if="!detail.currentMetadata?.repositoryUri" class="muted">
            {{ t("entry.noMetadata") }}
          </p>
          <template v-else>
            <p v-if="metadata.description" class="description">{{ metadata.description }}</p>
            <dl class="facts">
              <template v-if="metadata.publisherName || metadata.publisher">
                <dt>{{ t("entry.publisher") }}</dt>
                <dd>{{ metadata.publisherName || metadata.publisher }}</dd>
              </template>
              <template v-if="metadata.country">
                <dt>{{ t("entry.country") }}</dt>
                <dd>{{ countryLabel(metadata.country) }}</dd>
              </template>
              <template v-if="metadata.version">
                <dt>{{ t("entry.version") }}</dt>
                <dd>{{ metadata.version }}</dd>
              </template>
              <dt>{{ t("entry.repository") }}</dt>
              <dd class="mono small">{{ detail.currentMetadata.repositoryUri }}</dd>
            </dl>
          </template>
          <dl class="facts timing">
            <dt>{{ t("entry.registered") }}</dt>
            <dd :title="absoluteTime(detail.registrationTime)">
              {{ relativeTime(detail.registrationTime) }}
            </dd>
            <dt>{{ t("entry.lastPing") }}</dt>
            <dd :title="absoluteTime(detail.modificationTime)">
              {{ relativeTime(detail.modificationTime) }}
            </dd>
            <dt>{{ t("entry.lastRetrieval") }}</dt>
            <dd :title="absoluteTime(detail.lastRetrievalTime)">
              {{ relativeTime(detail.lastRetrievalTime) }}
            </dd>
            <template v-if="detail.health?.lastSuccessTime">
              <dt>{{ t("entry.lastSuccess") }}</dt>
              <dd :title="absoluteTime(detail.health.lastSuccessTime)">
                {{ relativeTime(detail.health.lastSuccessTime) }}
              </dd>
            </template>
            <template v-if="detail.health?.consecutiveFailures">
              <dt>{{ t("entry.failures") }}</dt>
              <dd class="failures">{{ detail.health.consecutiveFailures }}</dd>
            </template>
            <template v-if="detail.health?.nextCheckTime">
              <dt>{{ t("entry.nextCheck") }}</dt>
              <dd :title="absoluteTime(detail.health.nextCheckTime)">
                {{ relativeTime(detail.health.nextCheckTime) }}
              </dd>
            </template>
          </dl>
        </section>

        <section class="card">
          <h2>{{ t("entry.activity") }}</h2>
          <p class="muted small">{{ t("entry.activityHint") }}</p>
          <EventTimeline :events="detail.events" :detailed="session.isAdmin" />
        </section>
      </div>

      <section class="card stack">
        <h2>{{ t("entry.harvest") }}</h2>
        <p v-if="!detail.harvest?.nodes" class="muted">{{ t("entry.harvestNone") }}</p>
        <template v-else>
          <p>
            {{
              t(
                "entry.harvestSummary",
                { n: detail.harvest.nodes, when: relativeTime(detail.harvest.harvestedAt) },
                detail.harvest.nodes,
              )
            }}
          </p>
          <div class="row">
            <router-link
              v-if="info.data.value?.search && detail.permit === 'ACCEPTED'"
              class="btn primary"
              :to="{ name: 'search', query: { fdp: detail.uuid } }"
            >
              {{ t("search.searchFdp") }}
            </router-link>
            <button class="btn" :aria-expanded="showPreview" @click="showPreview = !showPreview">
              {{ showPreview ? t("entry.hidePreview") : t("entry.preview") }}
            </button>
            <span class="muted small">{{ t("entry.download") }}:</span>
            <button
              v-for="format in FORMATS"
              :key="format.key"
              class="btn sm"
              @click="download(format)"
            >
              {{ format.label }}
            </button>
          </div>
          <p v-if="showPreview && preview.isPending.value" class="muted">
            {{ t("common.loading") }}
          </p>
          <pre v-else-if="showPreview && preview.data.value" class="code">{{
            preview.data.value
          }}</pre>
        </template>
      </section>

      <ConformanceReport v-if="detail.permit === 'ACCEPTED'" :uuid="detail.uuid" />
    </template>
  </div>
</template>

<style scoped>
.back {
  color: var(--fair-text-muted);
}
.head {
  gap: var(--fair-space-2);
}
.options {
  flex-basis: 100%;
  gap: var(--fair-space-4);
}
.admin {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--fair-space-3);
  padding: var(--fair-space-4) var(--fair-space-6);
  border-left: 4px solid var(--tool-accent);
}
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: var(--fair-space-6);
  align-items: start;
}
.description {
  margin-bottom: var(--fair-space-4);
  color: var(--fair-text-strong);
}
.failures {
  color: var(--fair-warning);
  font-weight: var(--fair-weight-semibold);
}
.timing {
  margin-top: var(--fair-space-6);
  padding-top: var(--fair-space-4);
  border-top: 1px solid var(--fair-separator);
}
@media (max-width: 900px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
