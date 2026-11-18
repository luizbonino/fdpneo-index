<script setup lang="ts">
/**
 * A text input with suggestions from the harvested data (ARIA 1.2 combobox).
 *
 * Suggestions are values of `predicate` (or instances of `type`) that contain
 * what was typed, with their labels and how often they occur. For IRIs the
 * model holds the IRI and the field shows its label. Arrow keys move through
 * the list, Enter picks, Escape closes; free text is kept as typed.
 */
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { useI18n } from "vue-i18n";
import { sparqlApi, type Suggestion } from "@/sparql/api";
import { shortIri } from "@/sparql/results";

const props = defineProps<{
  modelValue: string;
  label: string;
  predicate?: string | null | undefined;
  type?: string | null | undefined;
  placeholder?: string | null | undefined;
  required?: boolean;
  inputId?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const { t } = useI18n();
const generated = useId();
const id = computed(() => props.inputId ?? generated);
const text = ref(props.modelValue);
const open = ref(false);
const active = ref(-1);
const items = ref<Suggestion[]>([]);
const loading = ref(false);
const labels = new Map<string, string>();
let timer: ReturnType<typeof setTimeout> | undefined;
let sequence = 0;

const canSuggest = computed(() => !!(props.predicate || props.type));

watch(
  () => props.modelValue,
  (value) => {
    if (value !== text.value && labels.get(value) !== text.value)
      text.value = labels.get(value) ?? value;
  },
);

async function load(q: string) {
  if (!canSuggest.value) return;
  const mine = ++sequence;
  loading.value = true;
  try {
    const found = await sparqlApi.suggest({
      ...(props.predicate ? { predicate: props.predicate } : {}),
      ...(props.type ? { type: props.type } : {}),
      q,
      limit: 12,
    });
    if (mine === sequence) {
      items.value = found;
      active.value = -1;
    }
  } catch {
    if (mine === sequence) items.value = [];
  } finally {
    if (mine === sequence) loading.value = false;
  }
}

function onInput(value: string) {
  text.value = value;
  emit("update:modelValue", value.trim());
  open.value = true;
  clearTimeout(timer);
  timer = setTimeout(() => void load(value.trim()), 200);
}

function onFocus() {
  if (!canSuggest.value) return;
  open.value = true;
  if (!items.value.length) void load(text.value.trim());
}

function pick(item: Suggestion) {
  labels.set(item.value, item.label);
  text.value = item.kind === "uri" ? item.label : item.value;
  emit("update:modelValue", item.value);
  open.value = false;
}

function onKey(event: KeyboardEvent) {
  if (!open.value || !items.value.length) {
    if (event.key === "ArrowDown" && canSuggest.value) {
      open.value = true;
      void load(text.value.trim());
    }
    return;
  }
  if (event.key === "ArrowDown") {
    active.value = (active.value + 1) % items.value.length;
    event.preventDefault();
  } else if (event.key === "ArrowUp") {
    active.value = (active.value - 1 + items.value.length) % items.value.length;
    event.preventDefault();
  } else if (event.key === "Enter" && active.value >= 0) {
    const item = items.value[active.value];
    if (item) pick(item);
    event.preventDefault();
  } else if (event.key === "Escape") {
    open.value = false;
  }
}

function onBlur() {
  setTimeout(() => (open.value = false), 150);
}

onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <div class="suggest">
    <input
      :id="id"
      class="input"
      :value="text"
      :placeholder="placeholder ?? undefined"
      :required="required"
      :aria-label="label"
      role="combobox"
      autocomplete="off"
      :aria-expanded="open && items.length > 0"
      :aria-controls="`${id}-list`"
      :aria-activedescendant="active >= 0 ? `${id}-opt-${active}` : undefined"
      aria-autocomplete="list"
      @input="onInput(($event.target as HTMLInputElement).value)"
      @focus="onFocus"
      @blur="onBlur"
      @keydown="onKey"
    />
    <ul v-show="open && (items.length || loading)" :id="`${id}-list`" class="list" role="listbox">
      <li v-if="loading && !items.length" class="muted small empty">{{ t("common.loading") }}</li>
      <li
        v-for="(item, index) in items"
        :id="`${id}-opt-${index}`"
        :key="item.value"
        role="option"
        :aria-selected="index === active"
        :class="{ active: index === active }"
        @mousedown.prevent="pick(item)"
      >
        <span class="label">{{ item.label }}</span>
        <span v-if="item.kind === 'uri'" class="mono small muted iri" :title="item.value">{{
          shortIri(item.value)
        }}</span>
        <span class="count small muted">{{ item.count.toLocaleString() }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.suggest {
  position: relative;
}
.list {
  position: absolute;
  z-index: 30;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  max-height: 280px;
  overflow-y: auto;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--fair-surface);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-md);
  box-shadow: var(--fair-shadow-3);
}
li {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0 var(--fair-space-2);
  padding: 6px 8px;
  border-radius: var(--fair-radius-sm, 4px);
  cursor: pointer;
}
li.active,
li:hover {
  background: var(--tool-accent-tint);
}
.label {
  color: var(--fair-text-strong);
  overflow-wrap: anywhere;
}
.iri {
  grid-column: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count {
  grid-column: 2;
  grid-row: 1;
}
.empty {
  display: block;
}
</style>
