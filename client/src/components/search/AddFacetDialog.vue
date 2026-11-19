<script setup lang="ts">
/**
 * "Add a filter": every property found in the harvested metadata of the
 * current types, most used first, over `GET /index/search/properties`.
 * Choosing one adds a facet on it (`prop:<IRI>`) to the search.
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { searchApi, type PropertyInfo } from "@/search/api";

const props = defineProps<{
  types: string[];
  /** Facets already on the page: not offered again. */
  present: string[];
}>();
const emit = defineEmits<{ add: [name: string]; close: [] }>();
const { t } = useI18n();

const dialog = ref<HTMLDialogElement>();
const filter = ref("");
const properties = ref<PropertyInfo[]>([]);
const loading = ref(false);
const error = ref("");

const shown = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  return properties.value.filter(
    (p) =>
      !props.present.includes(p.name) &&
      (!needle ||
        p.label.toLowerCase().includes(needle) ||
        p.property.toLowerCase().includes(needle) ||
        (p.curie ?? "").toLowerCase().includes(needle)),
  );
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    properties.value = (await searchApi.properties(props.types)).properties;
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    loading.value = false;
  }
}

function choose(name: string) {
  emit("add", name);
  dialog.value?.close();
}

onMounted(() => {
  dialog.value?.showModal();
  void load();
});
</script>

<template>
  <dialog
    ref="dialog"
    class="add-dialog"
    aria-labelledby="add-facet-title"
    @close="emit('close')"
    @cancel="emit('close')"
  >
    <header class="head">
      <h2 id="add-facet-title">{{ t("search.addFacet.title") }}</h2>
      <button type="button" class="btn ghost sm" @click="dialog?.close()">
        {{ t("common.close") }}
      </button>
    </header>
    <p class="muted small lead">{{ t("search.addFacet.lead") }}</p>
    <label class="filter">
      <span class="visually-hidden">{{ t("search.addFacet.filter") }}</span>
      <input
        v-model="filter"
        class="input"
        type="search"
        :placeholder="t('search.addFacet.filter')"
        autofocus
      />
    </label>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="loading" class="muted">{{ t("common.loading") }}</p>
    <p v-else-if="!shown.length" class="muted">{{ t("search.addFacet.none") }}</p>
    <ul :aria-busy="loading">
      <li v-for="property in shown" :key="property.name">
        <button
          type="button"
          class="option"
          :title="property.property"
          @click="choose(property.name)"
        >
          <span class="name">
            {{ property.label }}
            <code class="iri">{{ property.curie ?? property.property }}</code>
          </span>
          <span class="count">
            {{
              t("search.addFacet.resources", property.resources, {
                named: { n: property.resources.toLocaleString() },
              })
            }}
          </span>
        </button>
      </li>
    </ul>
  </dialog>
</template>

<style scoped>
.add-dialog {
  width: min(600px, calc(100vw - 32px));
  max-height: min(680px, calc(100vh - 64px));
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
}
.add-dialog::backdrop {
  background: rgb(0 0 0 / 0.35);
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--fair-space-2);
}
h2 {
  margin: 0;
  font-size: var(--fair-text-md, 1rem);
}
.lead {
  margin: var(--fair-space-2) 0 0;
}
.filter {
  display: block;
  margin: var(--fair-space-3) 0;
}
.filter .input {
  width: 100%;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 2px;
}
.option {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--fair-space-2);
  align-items: center;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--fair-radius-sm, 6px);
  background: none;
  color: inherit;
  font: inherit;
  font-size: var(--fair-text-sm);
  text-align: left;
  cursor: pointer;
}
.option:hover,
.option:focus-visible {
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.name {
  overflow-wrap: anywhere;
}
.iri {
  display: block;
  font-size: var(--fair-text-xs, 0.75rem);
  color: var(--fair-text-muted);
}
.count {
  color: var(--fair-text-muted);
  font-variant-numeric: tabular-nums;
  font-size: var(--fair-text-xs, 0.75rem);
  white-space: nowrap;
}
</style>
