<script setup lang="ts">
/**
 * One concept of a facet tree, with its narrower concepts behind a toggle.
 * A disclosure, not an ARIA tree: every checkbox and toggle is reached with Tab,
 * and the toggle says whether its branch is open (TASKS 8.9).
 */
import { computed, ref, useId } from "vue";
import { useI18n } from "vue-i18n";
import type { TreeNode } from "@/search/api";

const props = defineProps<{ node: TreeNode; selected: string[]; level: number }>();
const emit = defineEmits<{ toggle: [value: string] }>();
const { t } = useI18n();

const hasChildren = computed(() => props.node.children.length > 0);
// Open the first level, and any branch holding a selected concept.
function holdsSelected(node: TreeNode): boolean {
  return node.children.some((c) => props.selected.includes(c.value) || holdsSelected(c));
}
const open = ref(props.level === 1 || holdsSelected(props.node));
const branch = useId();
</script>

<template>
  <li class="node">
    <div class="row">
      <button
        v-if="hasChildren"
        type="button"
        class="twisty"
        :aria-expanded="open"
        :aria-controls="open ? branch : undefined"
        :aria-label="t('search.tree.toggle', { name: node.label })"
        @click="open = !open"
      >
        {{ open ? "▾" : "▸" }}
      </button>
      <span v-else class="twisty" aria-hidden="true" />
      <label class="option" :title="node.value">
        <input
          type="checkbox"
          :checked="selected.includes(node.value)"
          @change="emit('toggle', node.value)"
        />
        <span>{{ node.label }}</span>
        <span class="count">{{ node.count.toLocaleString() }}</span>
      </label>
    </div>
    <ul v-if="hasChildren && open" :id="branch" class="children">
      <FacetTreeNode
        v-for="child in node.children"
        :key="child.value"
        :node="child"
        :selected="selected"
        :level="level + 1"
        @toggle="(value) => emit('toggle', value)"
      />
    </ul>
  </li>
</template>

<style scoped>
.node {
  list-style: none;
}
.row {
  display: grid;
  grid-template-columns: 1.4em 1fr;
  align-items: center;
}
.twisty {
  border: 0;
  background: none;
  color: var(--fair-text-muted);
  cursor: pointer;
  padding: 0;
  font: inherit;
}
.option {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: var(--fair-space-2);
  align-items: center;
  padding: 3px 6px;
  font-size: var(--fair-text-sm);
  cursor: pointer;
  border-radius: var(--fair-radius-sm, 6px);
}
.option:hover {
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.count {
  color: var(--fair-text-muted);
  font-variant-numeric: tabular-nums;
  font-size: var(--fair-text-xs, 0.75rem);
}
.children {
  margin: 0;
  padding-left: 1.2em;
}
</style>
