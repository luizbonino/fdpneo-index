<script setup lang="ts">
/**
 * The network profile (TASKS 15.9), as YAML, for administrators: start from a
 * bundled profile, edit, save; the server checks it and says why when it is
 * not valid.
 */
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api } from "@/api";
import { ApiError } from "@/api/client";
import { confirm, toast } from "@/composables/feedback";

const { t } = useI18n();
const client = useQueryClient();
const view = useQuery({ queryKey: ["network-profile"], queryFn: api.networkProfile });
const examples = useQuery({ queryKey: ["network-examples"], queryFn: api.networkExamples });
const source = ref("");
const error = ref<string | null>(null);
const saving = ref(false);

watch(
  () => view.data.value,
  (value) => {
    if (value) source.value = value.source;
  },
  { immediate: true },
);

function start(name: string) {
  const found = examples.data.value?.find((e) => e.name === name);
  if (found) source.value = found.source;
}

async function save() {
  saving.value = true;
  error.value = null;
  try {
    const saved = await api.saveNetworkProfile(source.value);
    client.setQueryData(["network-profile"], saved);
    toast(t("networkProfile.saved"));
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message;
  } finally {
    saving.value = false;
  }
}

async function reset() {
  if (
    !(await confirm(t("networkProfile.resetConfirm"), { confirmLabel: t("networkProfile.reset") }))
  )
    return;
  const saved = await api.resetNetworkProfile();
  client.setQueryData(["network-profile"], saved);
  source.value = saved.source;
  toast(t("networkProfile.saved"));
}
</script>

<template>
  <section v-if="view.data.value" class="card stack" aria-labelledby="network-profile-h">
    <h2 id="network-profile-h">{{ t("networkProfile.heading") }}</h2>
    <p class="muted small">{{ t("networkProfile.lead") }}</p>
    <div class="row tools">
      <label class="row small">
        <span>{{ t("networkProfile.startFrom") }}</span>
        <select class="input select" @change="start(($event.target as HTMLSelectElement).value)">
          <option value="">—</option>
          <option v-for="e in examples.data.value ?? []" :key="e.name" :value="e.name">
            {{ e.name === "generic" ? t("networkProfile.generic") : e.title }}
          </option>
        </select>
      </label>
      <span class="spacer" />
      <router-link :to="{ name: 'expectations' }" class="small">{{
        t("networkProfile.view")
      }}</router-link>
    </div>
    <textarea
      v-model="source"
      class="input code"
      rows="18"
      spellcheck="false"
      :aria-label="t('networkProfile.heading')"
    />
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <div class="row">
      <button
        v-if="!view.data.value.isDefault"
        type="button"
        class="btn"
        :disabled="saving"
        @click="reset"
      >
        {{ t("networkProfile.reset") }}
      </button>
      <span class="spacer" />
      <button
        type="button"
        class="btn primary"
        :disabled="saving || source === view.data.value.source"
        @click="save"
      >
        {{ t("networkProfile.save") }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.tools {
  gap: var(--fair-space-3);
}
.tools label {
  gap: var(--fair-space-2);
}
.code {
  font-family: var(--fair-font-mono);
  font-size: var(--fair-text-base);
  min-height: 16rem;
  width: 100%;
}
.error {
  color: var(--fair-danger);
  white-space: pre-wrap;
}
</style>
