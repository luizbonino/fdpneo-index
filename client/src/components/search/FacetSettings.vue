<script setup lang="ts">
/**
 * Administrators: the search page's default facets (order, main / more /
 * hidden, labels of property facets) and whether visitors may add filters on
 * any property. Saved separately from the index settings
 * (`PUT /index/search/settings`).
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { confirm, toast } from "@/composables/feedback";
import {
  PROPERTY_FACET,
  isPropertyFacet,
  propertyLabel,
  searchApi,
  type FacetSetting,
  type Placement,
  type SearchSettings,
} from "@/search/api";
import AddFacetDialog from "@/components/search/AddFacetDialog.vue";

const { t } = useI18n();
const client = useQueryClient();

const landing = useQuery({
  queryKey: ["search-landing"],
  queryFn: searchApi.landing,
  staleTime: 60_000,
});
const stored = useQuery({ queryKey: ["search-settings"], queryFn: searchApi.settings });

const PLACEMENTS: Placement[] = ["main", "more", "hidden"];
const rows = ref<FacetSetting[]>([]);
const visitors = ref(true);
const hidden = ref("");
const defaultLanguage = ref("en");
// The fallback order as typed: language codes separated by commas (TASKS 11.8).
const fallback = ref("en");
const languages = computed(() => {
  const names = new Intl.DisplayNames([navigator.language || "en"], { type: "language" });
  return (landing.data.value?.languages ?? ["en"]).map((code) => ({
    code,
    name: names.of(code) ?? code,
  }));
});
const saving = ref(false);
const adding = ref(false);
const error = ref("");

/** Every facet in order: the stored list, then the profile's facets it leaves out. */
function load(settings: SearchSettings) {
  const shown = landing.data.value?.facets ?? [];
  const listed = settings.facets ?? [];
  const names = new Set(listed.map((f) => f.name));
  rows.value = [
    ...listed.map((f) => ({ ...f, label: { ...f.label }, help: { ...f.help } })),
    ...shown
      .filter((f) => !names.has(f.name))
      .map((f) => ({ name: f.name, placement: f.group, label: {}, help: {} })),
  ];
  visitors.value = settings.properties.visitors;
  hidden.value = settings.properties.hidden.join("\n");
  defaultLanguage.value = settings.defaultLanguage;
  fallback.value = (settings.languageFallback ?? ["en"]).join(", ");
}
watch(
  () => [stored.data.value, landing.data.value] as const,
  ([settings, info]) => settings && info && load(settings),
  { immediate: true },
);

function title(name: string): string {
  if (name === "fdp") return t("search.fdpFacet");
  const fields = landing.data.value?.fields ?? {};
  const label = fields[name]?.label;
  if (label) return label.en ?? Object.values(label)[0] ?? name;
  return isPropertyFacet(name) ? propertyLabel(name) : name;
}

function move(index: number, by: number) {
  const target = index + by;
  if (target < 0 || target >= rows.value.length) return;
  const next = [...rows.value];
  [next[index], next[target]] = [next[target]!, next[index]!];
  rows.value = next;
}

function remove(index: number) {
  rows.value = rows.value.filter((_, i) => i !== index);
}

function add(name: string) {
  rows.value = [...rows.value, { name, placement: "main", label: {}, help: {} }];
}

function setLabel(row: FacetSetting, value: string) {
  row.label = value.trim() ? { ...row.label, en: value } : {};
}

const present = computed(() => rows.value.map((r) => r.name));

async function save() {
  saving.value = true;
  error.value = "";
  try {
    const saved = await searchApi.saveSettings({
      facets: rows.value,
      properties: {
        visitors: visitors.value,
        hidden: hidden.value
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      },
      defaultLanguage: defaultLanguage.value,
      languageFallback: fallback.value
        .split(/[\s,]+/)
        .map((code) => code.trim().toLowerCase())
        .filter(Boolean),
    });
    client.setQueryData(["search-settings"], saved);
    await client.invalidateQueries({ queryKey: ["search-landing"] });
    toast(t("searchSettings.saved"));
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message;
  } finally {
    saving.value = false;
  }
}

async function reset() {
  if (
    !(await confirm(t("searchSettings.resetConfirm"), {
      confirmLabel: t("settings.reset"),
      danger: true,
    }))
  )
    return;
  const saved = await searchApi.resetSettings();
  client.setQueryData(["search-settings"], saved);
  await client.invalidateQueries({ queryKey: ["search-landing"] });
  toast(t("searchSettings.resetDone"));
}
</script>

<template>
  <section v-if="landing.isSuccess.value" class="card stack">
    <div class="row head">
      <h2>{{ t("searchSettings.title") }}</h2>
      <span
        v-if="stored.data.value"
        :class="['chip', stored.data.value.isDefault ? 'info' : 'warn']"
      >
        {{ stored.data.value.isDefault ? t("settings.default") : t("settings.customised") }}
      </span>
    </div>
    <p class="muted small">{{ t("searchSettings.lead") }}</p>

    <table class="facets">
      <thead>
        <tr>
          <th scope="col">{{ t("searchSettings.facet") }}</th>
          <th scope="col">{{ t("searchSettings.placement") }}</th>
          <th scope="col">
            <span class="visually-hidden">{{ t("searchSettings.actions") }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, index) in rows"
          :key="row.name"
          :class="{ off: row.placement === 'hidden' }"
        >
          <td>
            <template v-if="isPropertyFacet(row.name)">
              <input
                class="input label-input"
                :value="row.label.en ?? ''"
                :placeholder="title(row.name)"
                :aria-label="t('searchSettings.label', { name: title(row.name) })"
                @input="setLabel(row, ($event.target as HTMLInputElement).value)"
              />
              <code class="iri">{{ row.name.slice(PROPERTY_FACET.length) }}</code>
            </template>
            <span v-else>{{ title(row.name) }}</span>
          </td>
          <td>
            <select
              v-model="row.placement"
              class="input select"
              :aria-label="t('searchSettings.placementOf', { name: title(row.name) })"
            >
              <option v-for="placement in PLACEMENTS" :key="placement" :value="placement">
                {{ t(`searchSettings.placements.${placement}`) }}
              </option>
            </select>
          </td>
          <td class="buttons">
            <button
              type="button"
              class="btn ghost sm"
              :disabled="index === 0"
              :aria-label="t('searchSettings.up', { name: title(row.name) })"
              @click="move(index, -1)"
            >
              ↑
            </button>
            <button
              type="button"
              class="btn ghost sm"
              :disabled="index === rows.length - 1"
              :aria-label="t('searchSettings.down', { name: title(row.name) })"
              @click="move(index, 1)"
            >
              ↓
            </button>
            <button
              v-if="isPropertyFacet(row.name)"
              type="button"
              class="btn ghost sm"
              @click="remove(index)"
            >
              {{ t("search.remove") }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div>
      <button type="button" class="btn sm" @click="adding = true">
        {{ t("searchSettings.addProperty") }}
      </button>
    </div>

    <h3>{{ t("searchSettings.visitorsTitle") }}</h3>
    <label class="check">
      <input v-model="visitors" type="checkbox" />
      {{ t("searchSettings.visitors") }}
    </label>
    <div class="field">
      <label for="hidden-properties">{{ t("searchSettings.hidden") }}</label>
      <textarea
        id="hidden-properties"
        v-model="hidden"
        class="input mono"
        rows="3"
        :disabled="!visitors"
        placeholder="http://xmlns.com/foaf/0.1/mbox"
      />
      <span class="help">{{ t("searchSettings.hiddenHelp") }}</span>
    </div>

    <div class="field">
      <label for="default-language">{{ t("searchSettings.defaultLanguage") }}</label>
      <select id="default-language" v-model="defaultLanguage" class="input select">
        <option v-for="language in languages" :key="language.code" :value="language.code">
          {{ language.name }}
        </option>
      </select>
      <span class="help">{{ t("searchSettings.defaultLanguageHelp") }}</span>
    </div>

    <div class="field">
      <label for="language-fallback">{{ t("searchSettings.fallback") }}</label>
      <input
        id="language-fallback"
        v-model="fallback"
        class="input"
        autocomplete="off"
        placeholder="en, nl, de"
        aria-describedby="language-fallback-help"
      />
      <span id="language-fallback-help" class="help">{{ t("searchSettings.fallbackHelp") }}</span>
    </div>

    <p v-if="error" class="notice error">{{ error }}</p>
    <div class="row">
      <button type="button" class="btn danger" @click="reset">{{ t("settings.reset") }}</button>
      <span class="spacer" />
      <button type="button" class="btn primary" :disabled="saving" @click="save">
        {{ t("searchSettings.save") }}
      </button>
    </div>

    <AddFacetDialog
      v-if="adding"
      :types="[]"
      :present="present"
      @add="add"
      @close="adding = false"
    />
  </section>
</template>

<style scoped>
.head {
  justify-content: space-between;
}
h2,
h3 {
  margin: 0;
}
h3 {
  font-size: var(--fair-text-md, 1rem);
  margin-top: var(--fair-space-3);
}
.facets {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fair-text-sm);
}
.facets th {
  text-align: left;
  font-weight: 600;
  padding: 4px 6px;
  border-bottom: 1px solid var(--fair-border);
}
.facets td {
  padding: 4px 6px;
  border-bottom: 1px solid var(--fair-separator, var(--fair-border));
  vertical-align: middle;
}
.facets tr.off td:first-child {
  color: var(--fair-text-muted);
}
.buttons {
  white-space: nowrap;
  text-align: right;
}
.label-input {
  width: 100%;
}
.iri {
  display: block;
  font-size: var(--fair-text-xs, 0.75rem);
  color: var(--fair-text-muted);
  overflow-wrap: anywhere;
}
.spacer {
  flex: 1;
}
textarea {
  width: 100%;
}
@media (max-width: 560px) {
  .facets thead {
    display: none;
  }
  .facets tr {
    display: grid;
    grid-template-columns: 1fr auto;
    border-bottom: 1px solid var(--fair-border);
    padding: 4px 0;
  }
  .facets td {
    border: 0;
  }
  .facets td:first-child {
    grid-column: 1 / -1;
  }
}
</style>
