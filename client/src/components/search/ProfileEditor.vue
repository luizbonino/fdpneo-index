<script setup lang="ts">
/**
 * Administrators: changes to the search profile, as an overlay on the bundled
 * one (`PUT /index/search/profile`). Adding a field with `facet` makes a new
 * filter; saving re-projects every entry in the background. A bundled example
 * (HealthDCAT-AP) can be loaded into the editor to start from (TASKS 8.5).
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { confirm, toast } from "@/composables/feedback";
import { searchApi } from "@/search/api";

const { t } = useI18n();
const client = useQueryClient();
const profile = useQuery({ queryKey: ["search-profile"], queryFn: searchApi.profile });

const EXAMPLE = {
  fields: {
    fdpVersion: {
      kind: "literal",
      paths: ["dct:hasVersion"],
      facet: "more",
      label: { en: "FDP version" },
    },
  },
};

const text = ref("");
const error = ref("");
const saving = ref(false);
watch(
  () => profile.data.value,
  (value) => {
    if (value) text.value = value.overlay ? JSON.stringify(value.overlay, null, 2) : "";
  },
  { immediate: true },
);

const facets = computed(() =>
  Object.entries(profile.data.value?.profile.fields ?? {})
    .filter(([, spec]) => spec.facet)
    .map(([name]) => name),
);

async function save() {
  error.value = "";
  let overlay: unknown;
  try {
    overlay = text.value.trim() ? JSON.parse(text.value) : {};
  } catch (err) {
    error.value = t("profile.notJson", { error: (err as Error).message });
    return;
  }
  if (!overlay || typeof overlay !== "object" || Array.isArray(overlay)) {
    error.value = t("profile.notObject");
    return;
  }
  saving.value = true;
  try {
    const saved = await searchApi.saveProfile(overlay as Record<string, unknown>);
    client.setQueryData(["search-profile"], saved);
    await client.invalidateQueries({ queryKey: ["search-landing"] });
    toast(t("profile.saved"));
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message;
  } finally {
    saving.value = false;
  }
}

async function reset() {
  if (
    !(await confirm(t("profile.resetConfirm"), {
      confirmLabel: t("profile.reset"),
      danger: true,
    }))
  )
    return;
  const saved = await searchApi.resetProfile();
  client.setQueryData(["search-profile"], saved);
  await client.invalidateQueries({ queryKey: ["search-landing"] });
  toast(t("profile.resetDone"));
}

function example() {
  text.value = JSON.stringify(EXAMPLE, null, 2);
}

const loading = ref("");
async function load(name: string, title: string) {
  if (
    text.value.trim() &&
    !(await confirm(t("profile.loadConfirm", { name: title }), {
      confirmLabel: t("profile.load", { name: title }),
    }))
  )
    return;
  loading.value = name;
  error.value = "";
  try {
    const found = await searchApi.profileExample(name);
    text.value = JSON.stringify(found.overlay, null, 2);
    toast(t("profile.loaded", { name: title }));
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message;
  } finally {
    loading.value = "";
  }
}
</script>

<template>
  <section v-if="profile.isSuccess.value" class="card stack">
    <div class="row head">
      <h2>{{ t("profile.title") }}</h2>
      <span :class="['chip', profile.data.value?.overlay ? 'warn' : 'info']">
        {{ profile.data.value?.overlay ? t("settings.customised") : t("profile.bundled") }}
      </span>
    </div>
    <p class="muted small">{{ t("profile.lead") }}</p>
    <p class="small">
      {{ t("profile.facets", { n: facets.length }) }}
      <code v-for="name in facets" :key="name" class="name">{{ name }}</code>
    </p>
    <p v-if="profile.data.value?.indexing" class="notice warn small">{{ t("profile.indexing") }}</p>
    <div class="field">
      <label for="profile-overlay">{{ t("profile.overlay") }}</label>
      <textarea
        id="profile-overlay"
        v-model="text"
        class="input mono"
        rows="12"
        spellcheck="false"
        :aria-invalid="!!error"
        aria-describedby="profile-error"
      />
      <span class="help">{{ t("profile.help") }}</span>
      <span id="profile-error" class="error" role="alert">{{ error }}</span>
    </div>
    <div class="row">
      <button
        type="button"
        class="btn danger"
        :disabled="!profile.data.value?.overlay"
        @click="reset"
      >
        {{ t("profile.reset") }}
      </button>
      <button type="button" class="btn ghost" @click="example">{{ t("profile.example") }}</button>
      <button
        v-for="item in profile.data.value?.examples ?? []"
        :key="item.name"
        type="button"
        class="btn ghost"
        :disabled="loading === item.name"
        @click="load(item.name, item.title)"
      >
        {{ t("profile.load", { name: item.title }) }}
      </button>
      <span class="spacer" />
      <button type="button" class="btn primary" :disabled="saving" @click="save">
        {{ t("profile.save") }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.head {
  justify-content: space-between;
}
h2 {
  margin: 0;
}
.name {
  margin-left: 6px;
  font-size: var(--fair-text-xs, 0.75rem);
}
textarea {
  width: 100%;
  font-size: var(--fair-text-sm);
}
.spacer {
  flex: 1;
}
</style>
