<script setup lang="ts">
/**
 * One facet: a fieldset of checkboxes with counts, an ⓘ explanation, Clear,
 * and "Show more" (the dialog over every value). The first buckets show at
 * once; the rest of the page's buckets behind "Show all". A facet the reader
 * added (`removable`) shows even without values, with Remove. A number facet
 * also takes a minimum and a maximum; a yes/no facet reads Yes and No (TASKS 8.5).
 */
import { currentLocale } from "@/i18n";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { Bucket } from "@/search/api";
import HelpTip from "@/components/HelpTip.vue";
import StateBadge from "@/components/StateBadge.vue";

const props = defineProps<{
  name: string;
  title: string;
  help?: string | undefined;
  buckets: Bucket[];
  selected: string[];
  more: boolean;
  approximate?: boolean;
  removable?: boolean;
  subtitle?: string | null | undefined;
  kind?: "number" | "boolean" | undefined;
  range?: { min: string; max: string } | undefined;
}>();
const emit = defineEmits<{
  toggle: [value: string];
  clear: [];
  more: [];
  remove: [];
  range: [end: "min" | "max", value: string];
}>();
const { t } = useI18n();

const FIRST = 6;
const expanded = ref(false);
const visible = computed(() => {
  if (expanded.value) return props.buckets;
  const first = props.buckets.slice(0, FIRST);
  // Selected values always show, even when they are further down.
  const extra = props.buckets.slice(FIRST).filter((b) => props.selected.includes(b.value));
  return [...first, ...extra];
});
const hidden = computed(() => props.buckets.length - visible.value.length);
const ranged = computed(() => !!(props.range?.min || props.range?.max));

function label(bucket: Bucket): string {
  if (props.kind === "boolean") return bucket.value === "true" ? t("search.yes") : t("search.no");
  return bucket.label;
}

// A bound that is not a number is flagged on its input and not applied.
const invalid = ref({ min: false, max: false });
function setBound(end: "min" | "max", raw: string) {
  const value = raw.trim();
  invalid.value = { ...invalid.value, [end]: !!value && !Number.isFinite(Number(value)) };
  if (!invalid.value[end]) emit("range", end, value);
}
</script>

<template>
  <fieldset v-if="buckets.length || selected.length || ranged || removable" class="facet">
    <legend class="head">
      <span class="title">
        {{ title }}
        <code v-if="subtitle" class="subtitle">{{ subtitle }}</code>
      </span>
      <HelpTip v-if="help" :title="title" :text="help" />
      <span class="end">
        <button v-if="selected.length || ranged" type="button" class="link" @click="emit('clear')">
          {{ t("search.clear") }}
        </button>
        <button
          v-if="removable"
          type="button"
          class="link"
          :aria-label="t('search.removeFacet', { name: title })"
          @click="emit('remove')"
        >
          {{ t("search.remove") }}
        </button>
      </span>
    </legend>
    <p v-if="removable && !buckets.length && !selected.length" class="none">
      {{ t("search.noValues") }}
    </p>
    <ul>
      <li v-for="bucket in visible" :key="bucket.value">
        <label class="option" :title="bucket.value">
          <input
            type="checkbox"
            :checked="selected.includes(bucket.value)"
            @change="emit('toggle', bucket.value)"
          />
          <span class="label">{{ label(bucket) }}</span>
          <StateBadge
            v-if="name === 'fdp' && bucket.state && bucket.state !== 'ACTIVE'"
            class="state"
            :state="bucket.state"
          />
          <span class="count" :title="approximate ? t('search.estimated') : undefined">
            {{ approximate ? "≈" : "" }}{{ bucket.count.toLocaleString(currentLocale()) }}
          </span>
        </label>
      </li>
    </ul>
    <div v-if="kind === 'number'" class="bounds">
      <label>
        <span class="visually-hidden">{{ t("search.number.min", { name: title }) }}</span>
        <input
          class="input"
          type="text"
          inputmode="decimal"
          :placeholder="t('search.number.minShort')"
          :value="range?.min ?? ''"
          :aria-invalid="invalid.min || undefined"
          @change="setBound('min', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <span aria-hidden="true">–</span>
      <label>
        <span class="visually-hidden">{{ t("search.number.max", { name: title }) }}</span>
        <input
          class="input"
          type="text"
          inputmode="decimal"
          :placeholder="t('search.number.maxShort')"
          :value="range?.max ?? ''"
          :aria-invalid="invalid.max || undefined"
          @change="setBound('max', ($event.target as HTMLInputElement).value)"
        />
      </label>
    </div>
    <div class="actions">
      <button v-if="hidden > 0" type="button" class="link" @click="expanded = true">
        {{ t("search.showAll", { n: hidden }) }}
      </button>
      <button v-else-if="more" type="button" class="link" @click="emit('more')">
        {{ t("search.showMore") }}
      </button>
    </div>
  </fieldset>
</template>

<style scoped>
.facet {
  margin: 0;
  padding: var(--fair-space-3) 0;
  border: 0;
  border-top: 1px solid var(--fair-border);
  min-width: 0;
}
.head {
  display: flex;
  align-items: center;
  gap: var(--fair-space-1);
  width: 100%;
  padding: 0;
  margin: 0;
  min-height: 28px;
}
.title {
  font-weight: 600;
  font-size: var(--fair-text-sm);
}
.end {
  margin-left: auto;
  display: flex;
}
.subtitle {
  display: block;
  font-weight: 400;
  font-size: var(--fair-text-xs, 0.75rem);
  color: var(--fair-text-muted);
  overflow-wrap: anywhere;
}
.bounds {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 6px;
  margin-top: var(--fair-space-2);
}
.bounds .input {
  width: 100%;
  min-width: 0;
}
.bounds .input[aria-invalid="true"] {
  border-color: var(--fair-danger, #c53030);
}
.none {
  margin: var(--fair-space-1) 0 0;
  font-size: var(--fair-text-sm);
  color: var(--fair-text-muted);
}
ul {
  list-style: none;
  margin: var(--fair-space-1) 0 0;
  padding: 0;
  display: grid;
  gap: 2px;
}
.option {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: var(--fair-space-2);
  padding: 4px 6px;
  border-radius: var(--fair-radius-sm, 6px);
  cursor: pointer;
  font-size: var(--fair-text-sm);
}
.option:hover {
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.label {
  overflow-wrap: anywhere;
  line-height: 1.3;
}
.count {
  color: var(--fair-text-muted);
  font-variant-numeric: tabular-nums;
  font-size: var(--fair-text-xs, 0.75rem);
}
.state {
  transform: scale(0.85);
}
.actions {
  margin-top: var(--fair-space-1);
}
.link {
  border: 0;
  background: none;
  padding: 2px 6px;
  color: var(--tool-accent);
  font: inherit;
  font-size: var(--fair-text-sm);
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
</style>
