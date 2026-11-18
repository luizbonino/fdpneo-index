<script setup lang="ts">
/**
 * The approval queue: pending FDPs with their metadata, accepted or rejected
 * one by one or in bulk. Rejected FDPs are listed below for second thoughts.
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { api, type Entry, type Permit } from "@/api";
import { useEntries, useInvalidateEntries, useSettings } from "@/composables/queries";
import { toast } from "@/composables/feedback";
import StateBadge from "@/components/StateBadge.vue";
import StatusHelp from "@/components/StatusHelp.vue";
import { hostOf, relativeTime } from "@/utils/format";

const { t } = useI18n();
const invalidate = useInvalidateEntries();
const settings = useSettings();

const pending = useEntries({ permit: "pending", sort: "registrationTime,desc", size: 200 });
const rejected = useEntries({ permit: "rejected", sort: "modificationTime,desc", size: 50 });
const pendingEntries = computed<Entry[]>(() => pending.data.value?.content ?? []);

const selected = ref<Set<string>>(new Set());
watch(pendingEntries, (entries) => {
  const present = new Set(entries.map((e) => e.uuid));
  selected.value = new Set([...selected.value].filter((id) => present.has(id)));
});

const allSelected = computed(
  () => pendingEntries.value.length > 0 && selected.value.size === pendingEntries.value.length,
);

function toggle(uuid: string) {
  const next = new Set(selected.value);
  if (next.has(uuid)) next.delete(uuid);
  else next.add(uuid);
  selected.value = next;
}

function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(pendingEntries.value.map((e) => e.uuid));
}

const busy = ref(false);
async function decide(uuids: string[], permit: Permit) {
  busy.value = true;
  try {
    const results = await Promise.allSettled(uuids.map((uuid) => api.setPermit(uuid, permit)));
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length) toast(`${failed.length} failed`, "error");
    toast(t("approvals.done", { n: uuids.length - failed.length }));
    selected.value = new Set();
    await invalidate();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1 class="row">{{ t("approvals.title") }} <StatusHelp permits /></h1>
        <p class="lead">{{ t("approvals.lead") }}</p>
      </div>
    </header>

    <p v-if="settings.data.value?.autoPermit" class="notice">
      {{ t("approvals.autoOn") }}
      <router-link :to="{ name: 'settings' }">{{ t("nav.settings") }} →</router-link>
    </p>

    <section class="card flush">
      <div class="bar row">
        <label class="check">
          <input
            type="checkbox"
            :checked="allSelected"
            :disabled="!pendingEntries.length"
            @change="toggleAll"
          />
          {{ t("approvals.selectAll") }}
        </label>
        <span class="spacer" />
        <button
          class="btn primary"
          :disabled="busy || !selected.size"
          @click="decide([...selected], 'ACCEPTED')"
        >
          {{ t("approvals.acceptSelected") }}
        </button>
        <button
          class="btn"
          :disabled="busy || !selected.size"
          @click="decide([...selected], 'REJECTED')"
        >
          {{ t("approvals.rejectSelected") }}
        </button>
      </div>
      <p v-if="pending.isPending.value" class="muted pad">{{ t("common.loading") }}</p>
      <p v-else-if="!pendingEntries.length" class="muted pad">{{ t("approvals.empty") }}</p>
      <ul v-else class="queue">
        <li v-for="entry in pendingEntries" :key="entry.uuid">
          <input
            type="checkbox"
            :checked="selected.has(entry.uuid)"
            :aria-label="entry.title || entry.clientUrl"
            @change="toggle(entry.uuid)"
          />
          <div class="who">
            <router-link class="name" :to="{ name: 'entry', params: { uuid: entry.uuid } }">
              {{ entry.title || hostOf(entry.clientUrl) }}
            </router-link>
            <span class="mono small muted">{{ entry.clientUrl }}</span>
            <span class="small muted">{{ relativeTime(entry.registrationTime) }}</span>
          </div>
          <StateBadge :state="entry.state" />
          <div class="row">
            <button
              class="btn sm primary"
              :disabled="busy"
              @click="decide([entry.uuid], 'ACCEPTED')"
            >
              {{ t("entry.accept") }}
            </button>
            <button class="btn sm" :disabled="busy" @click="decide([entry.uuid], 'REJECTED')">
              {{ t("entry.reject") }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="rejected.data.value?.content.length" class="stack">
      <h2>{{ t("approvals.rejectedTitle") }}</h2>
      <p class="muted">{{ t("approvals.rejectedLead") }}</p>
      <div class="card flush">
        <ul class="queue">
          <li v-for="entry in rejected.data.value.content" :key="entry.uuid" class="no-check">
            <div class="who">
              <router-link class="name" :to="{ name: 'entry', params: { uuid: entry.uuid } }">
                {{ entry.title || hostOf(entry.clientUrl) }}
              </router-link>
              <span class="mono small muted">{{ entry.clientUrl }}</span>
            </div>
            <StateBadge :state="entry.state" />
            <button class="btn sm" :disabled="busy" @click="decide([entry.uuid], 'ACCEPTED')">
              {{ t("entry.accept") }}
            </button>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
.bar {
  padding: 12px 16px;
  border-bottom: 1px solid var(--fair-border);
  background: var(--fair-code-bg);
}
.spacer {
  flex: 1;
}
.pad {
  padding: 16px;
}
.queue {
  list-style: none;
  margin: 0;
  padding: 0;
}
.queue li {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: var(--fair-space-4);
  padding: 14px 16px;
  border-bottom: 1px solid var(--fair-separator);
}
.queue li.no-check {
  grid-template-columns: 1fr auto auto;
}
.queue li:last-child {
  border-bottom: 0;
}
.queue input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--tool-accent);
}
.who {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.name {
  font-weight: var(--fair-weight-semibold);
  color: var(--fair-text-strong);
}
@media (max-width: 720px) {
  .queue li {
    grid-template-columns: auto 1fr;
  }
}
</style>
