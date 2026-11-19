<script setup lang="ts">
/**
 * Administrators: which profiles harvested records are checked against, and
 * who may read the reports (TASKS 11.3), and the minimum each surface shows
 * visitors (TASKS 11.4). Kinds and files per profile are kept as they are;
 * the API changes them.
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { confirm, toast } from "@/composables/feedback";
import { conformanceApi, type ConformanceSettings } from "@/conformance/api";

const { t, locale } = useI18n();
const client = useQueryClient();
const packs = useQuery({ queryKey: ["conformance-packs"], queryFn: conformanceApi.packs });
const settings = useQuery({
  queryKey: ["conformance-settings"],
  queryFn: conformanceApi.settings,
  retry: false,
});

const active = ref<string[]>([]);
const reports = ref<"public" | "admin">("public");
const SURFACES = ["search", "sparql", "directory", "feed"] as const;
type Surface = (typeof SURFACES)[number];
const surfaces = ref<Record<Surface, string>>({ search: "", sparql: "", directory: "", feed: "" });
const activeIds = computed(() => active.value.map((key) => key.split("@")[0]!));
const saving = ref(false);

function load(value: ConformanceSettings) {
  active.value = value.packs.map((p) => p.key);
  reports.value = value.reports;
  surfaces.value = {
    search: value.surfaces.search ?? "",
    sparql: value.surfaces.sparql ?? "",
    directory: value.surfaces.directory ?? "",
    feed: value.surfaces.feed ?? "",
  };
}

function chosenSurfaces() {
  // A surface whose profile is no longer checked shows everything.
  const pick = (s: Surface) =>
    surfaces.value[s] && activeIds.value.includes(surfaces.value[s]) ? surfaces.value[s] : null;
  return {
    search: pick("search"),
    sparql: pick("sparql"),
    directory: pick("directory"),
    feed: pick("feed"),
  };
}
watch(
  () => settings.data.value,
  (value) => value && load(value),
  { immediate: true },
);

const dirty = computed(() => {
  const value = settings.data.value;
  if (!value) return false;
  return (
    value.reports !== reports.value ||
    value.packs.map((p) => p.key).join() !== active.value.join() ||
    JSON.stringify(value.surfaces) !== JSON.stringify(chosenSurfaces())
  );
});

function title(key: string): string {
  const pack = packs.data.value?.find((p) => p.key === key);
  if (!pack) return key;
  return pack.title[locale.value] ?? pack.title.en ?? key;
}

function toggle(key: string, on: boolean) {
  active.value = on ? [...active.value, key] : active.value.filter((k) => k !== key);
}

async function save() {
  const current = settings.data.value;
  if (!current) return;
  saving.value = true;
  try {
    // Keep each pack's kinds and files as they were.
    const kept = new Map(current.packs.map((p) => [p.key, p]));
    const saved = await conformanceApi.saveSettings({
      packs: active.value.map((key) => kept.get(key) ?? { key, kinds: null, files: null }),
      reports: reports.value,
      surfaces: chosenSurfaces(),
    });
    client.setQueryData(["conformance-settings"], saved);
    await client.invalidateQueries({ queryKey: ["conformance-entry"] });
    toast(t("conformance.settings.saved"));
  } catch (err) {
    toast(err instanceof ApiError ? err.message : (err as Error).message, "error");
  } finally {
    saving.value = false;
  }
}

async function validateAll() {
  if (
    !(await confirm(t("conformance.settings.validateAllConfirm"), {
      confirmLabel: t("conformance.settings.validateAll"),
    }))
  )
    return;
  await conformanceApi.validate();
  toast(t("conformance.validating"));
}
</script>

<template>
  <section v-if="settings.data.value" class="card stack" aria-labelledby="conformance-settings-h">
    <h2 id="conformance-settings-h">{{ t("conformance.settings.heading") }}</h2>
    <p class="muted small">{{ t("conformance.settings.lead") }}</p>
    <fieldset class="plain stack">
      <legend class="label">{{ t("conformance.settings.profiles") }}</legend>
      <label v-for="pack in packs.data.value ?? []" :key="pack.key" class="check pack">
        <input
          type="checkbox"
          :checked="active.includes(pack.key)"
          @change="toggle(pack.key, ($event.target as HTMLInputElement).checked)"
        />
        <span>
          <strong>{{ title(pack.key) }}</strong>
          <span class="muted small block">
            {{ pack.description[locale] ?? pack.description.en }}
            <template v-if="!pack.bundled">
              · {{ t("conformance.settings.operatorPack") }}</template
            >
          </span>
        </span>
      </label>
    </fieldset>
    <fieldset class="plain stack">
      <legend class="label">{{ t("conformance.settings.reports") }}</legend>
      <label class="check">
        <input v-model="reports" type="radio" value="public" />
        {{ t("conformance.settings.reportsPublic") }}
      </label>
      <label class="check">
        <input v-model="reports" type="radio" value="admin" />
        {{ t("conformance.settings.reportsAdmin") }}
      </label>
    </fieldset>
    <fieldset class="plain stack">
      <legend class="label">{{ t("conformance.settings.minimum") }}</legend>
      <p class="muted small">{{ t("conformance.settings.minimumLead") }}</p>
      <div class="surfaces">
        <template v-for="surface in SURFACES" :key="surface">
          <label :for="`minimum-${surface}`">{{
            t(`conformance.settings.surface.${surface}`)
          }}</label>
          <select :id="`minimum-${surface}`" v-model="surfaces[surface]" class="input">
            <option value="">{{ t("conformance.settings.everything") }}</option>
            <option v-for="key in active" :key="key" :value="key.split('@')[0]">
              {{ t("conformance.settings.onlyConforming", { profile: title(key) }) }}
            </option>
          </select>
        </template>
      </div>
    </fieldset>
    <div class="row">
      <button type="button" class="btn" @click="validateAll">
        {{ t("conformance.settings.validateAll") }}
      </button>
      <span class="spacer" />
      <button type="button" class="btn primary" :disabled="!dirty || saving" @click="save">
        {{ t("conformance.settings.save") }}
      </button>
    </div>
  </section>
</template>

<style scoped>
h2 {
  margin: 0;
}
.plain {
  border: 0;
  padding: 0;
  margin: 0;
}
.pack {
  align-items: flex-start;
}
.block {
  display: block;
}
.surfaces {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--fair-space-2) var(--fair-space-3);
  align-items: center;
}
@media (max-width: 600px) {
  .surfaces {
    grid-template-columns: 1fr;
  }
}
.spacer {
  flex: 1;
}
</style>
