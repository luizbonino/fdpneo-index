<script setup lang="ts">
/**
 * A facet's concepts as a tree (TASKS 8.12): each under its broader concept,
 * each counting the resources tagged with it or anything narrower. Ticking one
 * filters on "this or narrower" (`<field>^`).
 */
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { searchApi, type TreeNode } from "@/search/api";
import FacetTreeNode from "@/components/search/FacetTreeNode.vue";

const props = defineProps<{
  name: string;
  title: string;
  params: URLSearchParams;
  selected: string[];
}>();
const emit = defineEmits<{ toggle: [value: string]; close: [] }>();
const { t } = useI18n();

const dialog = ref<HTMLDialogElement>();
const nodes = ref<TreeNode[]>([]);
const loading = ref(true);
const error = ref("");

onMounted(async () => {
  dialog.value?.showModal();
  try {
    nodes.value = (await searchApi.tree(props.name, props.params)).tree;
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <dialog
    ref="dialog"
    class="tree-dialog"
    aria-labelledby="tree-title"
    @close="emit('close')"
    @cancel="emit('close')"
  >
    <header class="head">
      <h2 id="tree-title">{{ t("search.tree.title", { name: title }) }}</h2>
      <button type="button" class="btn ghost sm" @click="dialog?.close()">
        {{ t("common.close") }}
      </button>
    </header>
    <p class="muted small">{{ t("search.tree.lead") }}</p>
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="loading" class="muted">{{ t("common.loading") }}</p>
    <p v-else-if="!nodes.length" class="muted">{{ t("search.dialog.none") }}</p>
    <ul v-else role="tree" class="tree" :aria-label="title">
      <FacetTreeNode
        v-for="node in nodes"
        :key="node.value"
        :node="node"
        :selected="selected"
        :level="1"
        @toggle="(value) => emit('toggle', value)"
      />
    </ul>
  </dialog>
</template>

<style scoped>
.tree-dialog {
  width: min(620px, calc(100vw - 32px));
  max-height: min(700px, calc(100vh - 64px));
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
}
.tree-dialog::backdrop {
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
.tree {
  list-style: none;
  margin: var(--fair-space-2) 0 0;
  padding: 0;
}
</style>
