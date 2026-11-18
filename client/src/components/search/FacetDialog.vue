<script setup lang="ts">
/**
 * Every value of one facet, with a filter box: "Show more" opens this over
 * `GET /index/search/facets` (counts within the current search).
 */
import { onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { searchApi, type Bucket } from "@/search/api";

const props = defineProps<{
  name: string;
  title: string;
  params: URLSearchParams;
  selected: string[];
}>();
const emit = defineEmits<{ toggle: [value: string]; close: [] }>();
const { t } = useI18n();

const dialog = ref<HTMLDialogElement>();
const filter = ref("");
const buckets = ref<Bucket[]>([]);
const loading = ref(false);
const error = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    buckets.value = (await searchApi.facet(props.name, props.params, filter.value.trim())).buckets;
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    loading.value = false;
  }
}

let timer: ReturnType<typeof setTimeout> | undefined;
watch(filter, () => {
  clearTimeout(timer);
  timer = setTimeout(() => void load(), 250);
});

onMounted(() => {
  dialog.value?.showModal();
  void load();
});
</script>

<template>
  <dialog
    ref="dialog"
    class="facet-dialog"
    :aria-labelledby="`facet-${name}-title`"
    @close="emit('close')"
    @cancel="emit('close')"
  >
    <header class="head">
      <h2 :id="`facet-${name}-title`">{{ t("search.dialog.title", { name: title }) }}</h2>
      <button type="button" class="btn ghost sm" @click="dialog?.close()">
        {{ t("common.close") }}
      </button>
    </header>
    <label class="filter">
      <span class="visually-hidden">{{ t("search.dialog.filter") }}</span>
      <input
        v-model="filter"
        class="input"
        type="search"
        :placeholder="t('search.dialog.filter')"
        autofocus
      />
    </label>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="!loading && !buckets.length" class="muted">{{ t("search.dialog.none") }}</p>
    <ul :aria-busy="loading">
      <li v-for="bucket in buckets" :key="bucket.value">
        <label class="option" :title="bucket.value">
          <input
            type="checkbox"
            :checked="selected.includes(bucket.value)"
            @change="emit('toggle', bucket.value)"
          />
          <span>{{ bucket.label }}</span>
          <span class="count">{{ bucket.count.toLocaleString() }}</span>
        </label>
      </li>
    </ul>
  </dialog>
</template>

<style scoped>
.facet-dialog {
  width: min(560px, calc(100vw - 32px));
  max-height: min(640px, calc(100vh - 64px));
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
}
.facet-dialog::backdrop {
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
  grid-template-columns: auto 1fr auto;
  gap: var(--fair-space-2);
  align-items: center;
  padding: 4px 6px;
  font-size: var(--fair-text-sm);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.count {
  color: var(--fair-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
