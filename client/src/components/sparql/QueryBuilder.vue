<script setup lang="ts">
/**
 * The query builder: a custom query without typing SPARQL.
 *
 * 1. What to find (a class seen in the data) and, optionally, in which FDP.
 * 2. Conditions: a property (labels and counts from the data), an operator,
 *    and a value with suggestions; optionally on a related resource ("its
 *    distribution's media type").
 * 3. What to show: extra columns, or a count per value / per FDP.
 * 4. The generated SPARQL, live; "Edit as SPARQL" hands it to the editor.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { SparqlSchema } from "@/sparql/api";
import {
  generate,
  isIri,
  type BuilderModel,
  type Condition,
  type Operator,
} from "@/sparql/builder";
import SuggestInput from "./SuggestInput.vue";

const model = defineModel<BuilderModel>({ required: true });
defineProps<{ schema: SparqlSchema | null; busy: boolean }>();
const emit = defineEmits<{ run: [query: string]; edit: [query: string] }>();

const { t } = useI18n();

const DCAT = "http://www.w3.org/ns/dcat#";
const DCT = "http://purl.org/dc/terms/";
const HOPS: { value: string; key: string }[] = [
  { value: "", key: "itself" },
  { value: `${DCAT}distribution`, key: "distribution" },
  { value: `^${DCAT}dataset`, key: "catalog" },
  { value: `${DCT}publisher`, key: "publisher" },
  { value: `${DCAT}contactPoint`, key: "contact" },
  { value: `${DCT}temporal`, key: "temporal" },
];
const OPERATORS: Operator[] = [
  "is",
  "contains",
  "exists",
  "missing",
  "before",
  "after",
  "greater",
  "less",
];

const query = computed(() => {
  try {
    return generate(model.value);
  } catch (err) {
    return `# ${(err as Error).message}`;
  }
});

function update(change: Partial<BuilderModel>) {
  model.value = { ...model.value, ...change };
}

function setCondition(index: number, change: Partial<Condition>) {
  const conditions = model.value.conditions.map((c, i) => {
    if (i !== index) return c;
    const next = { ...c, ...change };
    // A value that is an absolute IRI is matched as an IRI, anything else as text.
    const first = next.values[0]?.trim() ?? "";
    next.kind = /^(https?|urn):/i.test(first) && isIri(first) ? "uri" : "literal";
    return next;
  });
  update({ conditions });
}

function addCondition() {
  update({
    conditions: [
      ...model.value.conditions,
      { property: `${DCAT}keyword`, operator: "contains", values: [""], kind: "literal" },
    ],
  });
}

function removeCondition(index: number) {
  update({ conditions: model.value.conditions.filter((_, i) => i !== index) });
}

function toggleColumn(iri: string, on: boolean) {
  const columns = on ? [...model.value.columns, iri] : model.value.columns.filter((c) => c !== iri);
  update({ columns });
}

const needsValue = (operator: Operator) => operator !== "exists" && operator !== "missing";
const valueType = (operator: Operator) =>
  operator === "before" || operator === "after"
    ? "date"
    : operator === "greater" || operator === "less"
      ? "number"
      : "text";
</script>

<template>
  <div class="builder">
    <fieldset class="step">
      <legend><span class="num">1</span>{{ t("sparql.builder.find") }}</legend>
      <div class="row wrap">
        <label class="field">
          <span class="small">{{ t("sparql.builder.type") }}</span>
          <select
            class="input select"
            :value="model.type"
            @change="update({ type: ($event.target as HTMLSelectElement).value })"
          >
            <option value="">{{ t("sparql.builder.anything") }}</option>
            <option v-for="c in schema?.classes ?? []" :key="c.iri" :value="c.iri">
              {{ c.label }} ({{ c.curie }}) · {{ c.count.toLocaleString() }}
            </option>
          </select>
        </label>
        <label class="field">
          <span class="small">{{ t("sparql.builder.inFdp") }}</span>
          <select
            class="input select"
            :value="model.fdp"
            @change="update({ fdp: ($event.target as HTMLSelectElement).value })"
          >
            <option value="">{{ t("sparql.library.anyFdp") }}</option>
            <option v-for="g in schema?.graphs ?? []" :key="g.iri" :value="g.iri">
              {{ g.title }}
            </option>
          </select>
        </label>
      </div>
    </fieldset>

    <fieldset class="step">
      <legend><span class="num">2</span>{{ t("sparql.builder.where") }}</legend>
      <p v-if="!model.conditions.length" class="small muted">
        {{ t("sparql.builder.noConditions") }}
      </p>
      <div v-for="(c, index) in model.conditions" :key="index" class="condition">
        <label class="field">
          <span class="small">{{ t("sparql.builder.on") }}</span>
          <select
            class="input select"
            :value="c.via ?? ''"
            @change="
              setCondition(index, { via: ($event.target as HTMLSelectElement).value || undefined })
            "
          >
            <option v-for="h in HOPS" :key="h.key" :value="h.value">
              {{ t(`sparql.builder.hops.${h.key}`) }}
            </option>
          </select>
        </label>
        <label class="field">
          <span class="small">{{ t("sparql.builder.property") }}</span>
          <select
            class="input select"
            :value="c.property"
            @change="setCondition(index, { property: ($event.target as HTMLSelectElement).value })"
          >
            <option
              v-for="p in schema?.properties ?? []"
              :key="p.iri"
              :value="p.iri"
              :title="p.iri"
            >
              {{ p.label }} ({{ p.curie }}) · {{ p.count.toLocaleString() }}
            </option>
          </select>
        </label>
        <label class="field">
          <span class="small">{{ t("sparql.builder.operator") }}</span>
          <select
            class="input select"
            :value="c.operator"
            @change="
              setCondition(index, {
                operator: ($event.target as HTMLSelectElement).value as Operator,
              })
            "
          >
            <option v-for="op in OPERATORS" :key="op" :value="op">
              {{ t(`sparql.builder.ops.${op}`) }}
            </option>
          </select>
        </label>
        <div v-if="needsValue(c.operator)" class="field value">
          <span class="small">{{ t("sparql.builder.value") }}</span>
          <SuggestInput
            v-if="valueType(c.operator) === 'text'"
            :model-value="c.values[0] ?? ''"
            :label="t('sparql.builder.value')"
            :predicate="c.via ? null : c.property"
            :placeholder="t('sparql.library.typeToSearch')"
            @update:model-value="setCondition(index, { values: [$event] })"
          />
          <input
            v-else
            class="input"
            :type="valueType(c.operator)"
            :value="c.values[0] ?? ''"
            :aria-label="t('sparql.builder.value')"
            @input="setCondition(index, { values: [($event.target as HTMLInputElement).value] })"
          />
        </div>
        <button
          class="btn ghost sm remove"
          :aria-label="t('sparql.builder.remove')"
          @click="removeCondition(index)"
        >
          ✕
        </button>
      </div>
      <button class="btn sm" @click="addCondition">+ {{ t("sparql.builder.addCondition") }}</button>
    </fieldset>

    <fieldset class="step">
      <legend><span class="num">3</span>{{ t("sparql.builder.show") }}</legend>
      <div class="row wrap">
        <label class="field">
          <span class="small">{{ t("sparql.builder.countBy") }}</span>
          <select
            class="input select"
            :value="model.countBy"
            @change="update({ countBy: ($event.target as HTMLSelectElement).value })"
          >
            <option value="">{{ t("sparql.builder.list") }}</option>
            <option value="fdp">{{ t("sparql.builder.perFdp") }}</option>
            <option v-for="p in schema?.properties ?? []" :key="p.iri" :value="p.iri">
              {{ t("sparql.builder.per", { label: p.label }) }} ({{ p.curie }})
            </option>
          </select>
        </label>
        <label class="field narrow">
          <span class="small">{{ t("sparql.builder.limit") }}</span>
          <input
            class="input"
            type="number"
            min="1"
            max="10000"
            :value="model.limit"
            @input="update({ limit: Number(($event.target as HTMLInputElement).value) })"
          />
        </label>
      </div>
      <details v-if="!model.countBy" class="columns">
        <summary class="small">
          {{ t("sparql.builder.columns", { n: model.columns.length }) }}
        </summary>
        <div class="checks">
          <label v-for="p in (schema?.properties ?? []).slice(0, 40)" :key="p.iri" class="small">
            <input
              type="checkbox"
              :checked="model.columns.includes(p.iri)"
              @change="toggleColumn(p.iri, ($event.target as HTMLInputElement).checked)"
            />
            {{ p.label }} <span class="muted mono">{{ p.curie }}</span>
          </label>
        </div>
      </details>
    </fieldset>

    <fieldset class="step">
      <legend><span class="num">4</span>{{ t("sparql.builder.generated") }}</legend>
      <pre class="preview mono" aria-live="polite">{{ query }}</pre>
      <div class="row wrap">
        <button
          class="btn primary"
          :disabled="busy || query.startsWith('#')"
          @click="emit('run', query)"
        >
          {{ t("sparql.run") }}
        </button>
        <button class="btn" :disabled="query.startsWith('#')" @click="emit('edit', query)">
          {{ t("sparql.builder.editAsSparql") }}
        </button>
        <span class="small muted">{{ t("sparql.builder.oneWay") }}</span>
      </div>
    </fieldset>
  </div>
</template>

<style scoped>
.builder {
  display: grid;
  gap: var(--fair-space-4);
}
.step {
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  padding: var(--fair-space-3) var(--fair-space-4) var(--fair-space-4);
  margin: 0;
  display: grid;
  gap: var(--fair-space-3);
  min-width: 0;
}
legend {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 6px;
  font-weight: var(--fair-weight-semibold);
  color: var(--fair-text-strong);
}
.num {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--tool-accent);
  color: var(--fair-text-on-accent);
  font-size: 12px;
}
.wrap {
  flex-wrap: wrap;
  align-items: end;
}
.field {
  display: grid;
  gap: 4px;
  min-width: 0;
  flex: 1 1 220px;
}
.narrow {
  flex: 0 0 120px;
}
.condition {
  display: grid;
  grid-template-columns:
    minmax(140px, 0.8fr) minmax(180px, 1.4fr) minmax(130px, 0.8fr) minmax(180px, 1.4fr)
    auto;
  gap: var(--fair-space-2);
  align-items: end;
  padding-bottom: var(--fair-space-2);
  border-bottom: 1px dashed var(--fair-separator);
}
.remove {
  align-self: end;
}
.checks {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 4px var(--fair-space-3);
  padding-top: var(--fair-space-2);
}
.preview {
  margin: 0;
  padding: var(--fair-space-3);
  background: var(--fair-code-bg);
  border-radius: var(--fair-radius-md);
  font-size: 12.5px;
  overflow-x: auto;
  white-space: pre;
}
@media (max-width: 860px) {
  .condition {
    grid-template-columns: 1fr 1fr;
  }
  .condition .value {
    grid-column: 1 / -1;
  }
  .condition .remove {
    grid-column: 2;
    justify-self: end;
  }
}
</style>
