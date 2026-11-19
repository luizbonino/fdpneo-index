<script setup lang="ts">
/**
 * The search box with suggestions (TASKS 8.2): an ARIA 1.2 combobox. From two
 * characters on it lists resources whose title matches and filter values
 * (keywords, subjects, themes, publishers) whose name starts with the text.
 * Arrow keys move, Enter picks the active suggestion or searches the text,
 * Escape closes the list.
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { searchApi, type Suggestion } from "@/search/api";

const props = defineProps<{ types: string[]; lang?: string | undefined }>();
const text = defineModel<string>({ required: true });
const emit = defineEmits<{
  submit: [];
  value: [field: string, value: string];
  resource: [id: string];
}>();
const { t } = useI18n();

const suggestions = ref<Suggestion[]>([]);
const open = ref(false);
const active = ref(-1);
let timer: ReturnType<typeof setTimeout> | undefined;
let asked = 0;

watch(text, (value) => {
  clearTimeout(timer);
  if (value.trim().length < 2) {
    suggestions.value = [];
    open.value = false;
    return;
  }
  timer = setTimeout(() => void load(value), 180);
});

async function load(value: string) {
  const ticket = ++asked;
  try {
    const result = await searchApi.suggest(value.trim(), props.types, props.lang);
    if (ticket !== asked) return; // an older answer arriving late
    suggestions.value = result.suggestions;
    active.value = -1;
    open.value = result.suggestions.length > 0 && document.activeElement === input.value;
  } catch {
    suggestions.value = [];
    open.value = false;
  }
}

const input = ref<HTMLInputElement>();
const listId = "search-suggestions";
const activeId = computed(() => (active.value >= 0 ? `suggestion-${active.value}` : undefined));

function choose(index: number) {
  const picked = suggestions.value[index];
  open.value = false;
  if (!picked) return;
  if (picked.kind === "resource") emit("resource", picked.id);
  else if (picked.kind === "query") {
    // The same title in several FDPs: search for it as a phrase.
    text.value = `"${picked.text}"`;
    emit("submit");
  } else emit("value", picked.field, picked.value);
}

function keydown(event: KeyboardEvent) {
  const count = suggestions.value.length;
  if (event.key === "ArrowDown" && count) {
    event.preventDefault();
    open.value = true;
    active.value = (active.value + 1) % count;
  } else if (event.key === "ArrowUp" && count) {
    event.preventDefault();
    open.value = true;
    active.value = active.value <= 0 ? count - 1 : active.value - 1;
  } else if (event.key === "Escape" && open.value) {
    event.preventDefault();
    open.value = false;
    active.value = -1;
  } else if (event.key === "Enter") {
    event.preventDefault();
    if (open.value && active.value >= 0) choose(active.value);
    else {
      open.value = false;
      emit("submit");
    }
  }
}

function blur() {
  // Let a click on a suggestion land first.
  setTimeout(() => (open.value = false), 150);
}

function fieldName(field: string): string {
  return t(`search.suggest.fields.${field}`, field);
}
</script>

<template>
  <div class="suggest">
    <label class="box">
      <span class="visually-hidden">{{ t("search.placeholder") }}</span>
      <input
        ref="input"
        v-model="text"
        class="input"
        type="search"
        name="q"
        maxlength="512"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="open"
        :aria-controls="listId"
        :aria-activedescendant="activeId"
        :placeholder="t('search.placeholder')"
        autocomplete="off"
        @keydown="keydown"
        @focus="open = suggestions.length > 0"
        @blur="blur"
      />
    </label>
    <ul
      v-show="open"
      :id="listId"
      role="listbox"
      class="list"
      :aria-label="t('search.suggest.label')"
    >
      <li
        v-for="(item, index) in suggestions"
        :id="`suggestion-${index}`"
        :key="`${item.kind}-${index}`"
        role="option"
        :aria-selected="index === active"
        :class="['option', { active: index === active }]"
        @mousedown.prevent="choose(index)"
      >
        <template v-if="item.kind === 'resource'">
          <span class="what">{{ t(`search.kinds.${item.type}`) }}</span>
          <span class="text">{{ item.title }}</span>
        </template>
        <template v-else-if="item.kind === 'query'">
          <span class="what">{{ t("search.suggest.titles") }}</span>
          <span class="text">{{ item.text }}</span>
          <span class="count">{{ item.count.toLocaleString() }}</span>
        </template>
        <template v-else>
          <span class="what">{{ fieldName(item.field) }}</span>
          <span class="text">{{ item.label }}</span>
          <span class="count">{{ item.count.toLocaleString() }}</span>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.suggest {
  position: relative;
  flex: 1;
  min-width: 0;
}
.box {
  display: block;
}
.box .input {
  width: 100%;
  font-size: 1.05rem;
  padding-block: 10px;
}
.list {
  position: absolute;
  z-index: 20;
  inset-inline: 0;
  top: calc(100% + 4px);
  margin: 0;
  padding: 4px;
  list-style: none;
  background: var(--fair-surface);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-md, 8px);
  box-shadow: var(--fair-shadow-4);
  max-height: 360px;
  overflow-y: auto;
}
.option {
  display: grid;
  grid-template-columns: 8.5em 1fr auto;
  gap: var(--fair-space-2);
  align-items: baseline;
  padding: 6px 8px;
  border-radius: var(--fair-radius-sm, 6px);
  cursor: pointer;
  font-size: var(--fair-text-sm);
}
.option.active,
.option:hover {
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.what {
  color: var(--fair-text-muted);
  font-size: var(--fair-text-xs, 0.75rem);
}
.text {
  overflow-wrap: anywhere;
}
.count {
  color: var(--fair-text-muted);
  font-variant-numeric: tabular-nums;
  font-size: var(--fair-text-xs, 0.75rem);
}
@media (max-width: 560px) {
  .option {
    grid-template-columns: 1fr auto;
  }
  .what {
    grid-column: 1 / -1;
  }
}
</style>
