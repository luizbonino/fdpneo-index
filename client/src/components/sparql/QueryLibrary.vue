<script setup lang="ts">
/**
 * The query library: ready-made questions with a form for their parameters.
 * No SPARQL is needed; "Show query" reveals it and "Open in editor" hands it
 * over for changes. The server binds parameters safely (VALUES), so whatever
 * is typed here cannot change the query's structure.
 */
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { sparqlApi, type Example, type Library, type SparqlSchema } from "@/sparql/api";
import SuggestInput from "./SuggestInput.vue";

const props = defineProps<{
  library: Library | null;
  schema: SparqlSchema | null;
  selected: string | null;
  initialValues: Record<string, string>;
  busy: boolean;
}>();
const emit = defineEmits<{
  select: [id: string | null, values: Record<string, string>];
  run: [query: string, example: Example, values: Record<string, string>];
  edit: [query: string];
}>();

const { t } = useI18n();
const values = reactive<Record<string, string>>({ ...props.initialValues });
const shown = ref<string | null>(null);
const error = ref<string | null>(null);

const example = computed(
  () => props.library?.examples.find((e) => e.id === props.selected) ?? null,
);
const groups = computed(() =>
  (props.library?.groups ?? []).map((group) => ({
    name: group,
    examples: (props.library?.examples ?? []).filter((e) => e.group === group),
  })),
);

watch(
  () => props.selected,
  (id, previous) => {
    if (id === previous) return;
    shown.value = null;
    error.value = null;
    for (const key of Object.keys(values)) delete values[key];
    const chosen = props.library?.examples.find((e) => e.id === id);
    for (const p of chosen?.parameters ?? [])
      values[p.name] = props.initialValues[p.name] ?? p.default ?? "";
  },
  { immediate: true },
);

function filled(): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
}

async function expanded(): Promise<string | null> {
  if (!example.value) return null;
  error.value = null;
  try {
    return (await sparqlApi.expand(example.value.id, filled())).query;
  } catch (err) {
    error.value = (err as Error).message;
    return null;
  }
}

async function run() {
  const query = await expanded();
  if (query && example.value) {
    emit("select", example.value.id, filled());
    emit("run", query, example.value, filled());
  }
}

async function show() {
  shown.value = shown.value ? null : await expanded();
}

async function edit() {
  const query = await expanded();
  if (query) emit("edit", query);
}
</script>

<template>
  <div class="library">
    <div v-if="!library" class="muted">{{ t("common.loading") }}</div>

    <template v-else-if="!example">
      <section v-for="group in groups" :key="group.name" class="group">
        <h2>{{ group.name }}</h2>
        <div class="cards">
          <button
            v-for="item in group.examples"
            :key="item.id"
            class="card pick"
            @click="emit('select', item.id, {})"
          >
            <strong>{{ item.title }}</strong>
            <span class="small muted">{{ item.description }}</span>
            <span v-if="item.parameters?.length" class="small params">
              {{ (item.parameters ?? []).map((p) => p.label).join(" · ") }}
            </span>
          </button>
        </div>
      </section>
    </template>

    <section v-else class="chosen">
      <button class="btn ghost sm back" @click="emit('select', null, {})">
        ← {{ t("sparql.library.all") }}
      </button>
      <header>
        <span class="eyebrow">{{ example.group }}</span>
        <h2>{{ example.title }}</h2>
        <p class="muted">{{ example.description }}</p>
      </header>
      <form class="form" @submit.prevent="run">
        <div v-for="p in example.parameters ?? []" :key="p.name" class="field">
          <label :for="`param-${p.name}`" class="small">
            {{ p.label }}<span v-if="p.required" aria-hidden="true"> *</span>
          </label>
          <select
            v-if="p.type === 'choice'"
            :id="`param-${p.name}`"
            v-model="values[p.name]"
            class="input select"
          >
            <option v-if="!p.required" value="">{{ t("sparql.library.any") }}</option>
            <option v-for="c in p.choices" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
          <select
            v-else-if="p.type === 'fdp'"
            :id="`param-${p.name}`"
            v-model="values[p.name]"
            class="input select"
          >
            <option value="">{{ t("sparql.library.anyFdp") }}</option>
            <option v-for="g in schema?.graphs ?? []" :key="g.iri" :value="g.iri">
              {{ g.title }}
            </option>
          </select>
          <input
            v-else-if="p.type === 'date'"
            :id="`param-${p.name}`"
            v-model="values[p.name]"
            class="input"
            type="date"
            :required="p.required"
          />
          <input
            v-else-if="p.type === 'integer'"
            :id="`param-${p.name}`"
            v-model="values[p.name]"
            class="input"
            type="number"
            step="1"
            :required="p.required"
          />
          <SuggestInput
            v-else-if="p.suggest"
            :model-value="values[p.name] ?? ''"
            :input-id="`param-${p.name}`"
            :label="p.label"
            :predicate="p.suggest.predicate"
            :type="p.suggest.type"
            :placeholder="p.placeholder ?? t('sparql.library.typeToSearch')"
            :required="p.required"
            @update:model-value="values[p.name] = $event"
          />
          <input
            v-else
            :id="`param-${p.name}`"
            v-model="values[p.name]"
            class="input"
            :type="p.type === 'iri' ? 'url' : 'text'"
            :placeholder="p.placeholder ?? undefined"
            :required="p.required"
          />
          <span v-if="p.help" class="small muted">{{ p.help }}</span>
        </div>
        <p v-if="error" class="notice error small" role="alert">{{ error }}</p>
        <div class="row actions">
          <button class="btn primary" type="submit" :disabled="busy">{{ t("sparql.run") }}</button>
          <button class="btn" type="button" @click="show">
            {{ shown ? t("sparql.library.hideQuery") : t("sparql.library.showQuery") }}
          </button>
          <button class="btn ghost" type="button" @click="edit">
            {{ t("sparql.library.openInEditor") }}
          </button>
        </div>
      </form>
      <pre v-if="shown" class="query mono">{{ shown }}</pre>
    </section>
  </div>
</template>

<style scoped>
.library {
  display: grid;
  gap: var(--fair-space-5);
}
.group h2 {
  margin: 0 0 var(--fair-space-2);
  font-size: var(--fair-text-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fair-text-muted);
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--fair-space-3);
}
.pick {
  display: grid;
  gap: 4px;
  text-align: left;
  cursor: pointer;
  border: 1px solid var(--fair-border);
  color: var(--fair-text);
  font: inherit;
  align-content: start;
}
.pick:hover,
.pick:focus-visible {
  border-color: var(--tool-accent);
  background: var(--tool-accent-tint);
}
.pick strong {
  color: var(--fair-text-strong);
}
.params {
  color: var(--tool-accent);
}
.chosen {
  display: grid;
  gap: var(--fair-space-3);
}
.chosen header h2 {
  margin: 2px 0;
  font-size: var(--fair-text-lg, 1.15rem);
}
.chosen header p {
  margin: 0;
}
.back {
  justify-self: start;
}
.form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--fair-space-3);
  align-items: end;
}
.field {
  display: grid;
  gap: 4px;
}
.actions,
.form > .notice {
  grid-column: 1 / -1;
  flex-wrap: wrap;
}
.query {
  margin: 0;
  padding: var(--fair-space-3);
  background: var(--fair-code-bg);
  border-radius: var(--fair-radius-md);
  font-size: 12.5px;
  overflow-x: auto;
}
</style>
