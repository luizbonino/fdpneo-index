<script setup lang="ts">
/**
 * One search hit: type, title (to the resource page), where it comes from
 * (FDP with its state, catalog), a description, theme, subject and keyword chips, the
 * formats of a dataset's distributions, the profile's key fields, and its
 * copies in other FDPs (TASKS 8.8).
 *
 * Every link to the source is checked to be http(s): harvested IRIs are
 * attacker-chosen.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { Hit, ValueView } from "@/search/api";
import { isLinkable } from "@/sparql/results";
import StateBadge from "@/components/StateBadge.vue";
import { absoluteTime, formatPeriod, relativeTime } from "@/utils/format";

const props = defineProps<{
  hit: Hit;
  cards: string[];
  fieldLabel: (name: string) => string;
}>();
const emit = defineEmits<{ filter: [name: string, value: string] }>();
const { t } = useI18n();

const MAX_KEYWORDS = 6;
const MAX_SUBJECTS = 4;

const themes = computed(() => props.hit.values.theme ?? []);
const subjects = computed(() => (props.hit.values.subject ?? []).slice(0, MAX_SUBJECTS));
const keywords = computed(() => (props.hit.values.keyword ?? []).slice(0, MAX_KEYWORDS));
const formats = computed(() => {
  const seen = new Map<string, string>();
  for (const v of [...(props.hit.values.format ?? []), ...(props.hit.values.mediaType ?? [])]) {
    if (!seen.has(v.label.toLowerCase())) seen.set(v.label.toLowerCase(), v.label);
  }
  return [...seen.values()].slice(0, 8);
});
const distributions = computed(() => props.hit.facts.distributionCount ?? 0);
const keyFields = computed(() =>
  props.cards
    .filter((name) => name !== "format" || !formats.value.length)
    .map((name) => {
      if (name === "datasetCount" || name === "distributionCount") {
        const count = props.hit.facts[name];
        return count === undefined ? null : { name, text: count.toLocaleString() };
      }
      const values: ValueView[] = props.hit.values[name] ?? [];
      if (!values.length) return null;
      const text = values
        .slice(0, 3)
        .map((v) =>
          v.value === "true" ? t("search.yes") : v.value === "false" ? t("search.no") : v.label,
        )
        .join(", ");
      return { name, text: values.length > 3 ? `${text} +${values.length - 3}` : text };
    })
    .filter((item): item is { name: string; text: string } => item !== null),
);
const date = computed(() => props.hit.modified ?? props.hit.issued);
const period = computed(() => formatPeriod(props.hit.temporal, t));
const down = computed(() => !["ACTIVE", "UNKNOWN"].includes(props.hit.fdp.state));
const source = computed(() =>
  isLinkable(props.hit.landingPage ?? "")
    ? props.hit.landingPage
    : isLinkable(props.hit.sourceUrl)
      ? props.hit.sourceUrl
      : null,
);
</script>

<template>
  <article :class="['result', `kind-${hit.type}`]">
    <div class="meta">
      <span class="kind">{{ t(`search.kinds.${hit.type}`) }}</span>
      <router-link class="fdp" :to="{ name: 'entry', params: { uuid: hit.fdp.uuid } }">
        {{ hit.fdp.title }}
      </router-link>
      <StateBadge v-if="down" :state="hit.fdp.state" />
      <template v-if="hit.catalog">
        <span class="sep" aria-hidden="true">›</span>
        <router-link class="catalog" :to="{ name: 'resource', params: { id: hit.catalog.id } }">
          {{ hit.catalog.title }}
        </router-link>
      </template>
    </div>
    <h3>
      <router-link
        :to="{ name: 'resource', params: { id: hit.id } }"
        :lang="hit.titleLang || undefined"
      >
        <template v-if="hit.highlight">
          <template v-for="(part, i) in hit.highlight.title" :key="i">
            <mark v-if="part.match">{{ part.text }}</mark>
            <template v-else>{{ part.text }}</template>
          </template>
        </template>
        <template v-else>{{ hit.title }}</template>
      </router-link>
    </h3>
    <p v-if="hit.highlight?.description" class="description">
      <template v-for="(part, i) in hit.highlight.description" :key="i">
        <mark v-if="part.match">{{ part.text }}</mark>
        <template v-else>{{ part.text }}</template>
      </template>
    </p>
    <p v-else-if="hit.description" class="description">{{ hit.description }}</p>
    <ul v-if="themes.length || subjects.length || keywords.length" class="chips">
      <li v-for="theme in themes" :key="theme.value">
        <button
          type="button"
          class="tag theme"
          :title="theme.value"
          @click="emit('filter', 'theme', theme.value)"
        >
          {{ theme.label }}
        </button>
      </li>
      <li v-for="subject in subjects" :key="subject.value">
        <button
          type="button"
          class="tag subject"
          :title="subject.value"
          @click="emit('filter', 'subject', subject.value)"
        >
          {{ subject.label }}
        </button>
      </li>
      <li v-for="keyword in keywords" :key="keyword.value">
        <button type="button" class="tag" @click="emit('filter', 'keyword', keyword.value)">
          {{ keyword.label }}
        </button>
      </li>
    </ul>
    <p v-if="formats.length || distributions" class="formats">
      <span v-for="format in formats" :key="format" class="format">{{ format }}</span>
      <span v-if="distributions" class="muted">{{ t("search.distributions", distributions) }}</span>
    </p>
    <footer class="foot">
      <dl v-if="keyFields.length" class="keys">
        <div v-for="item in keyFields" :key="item.name">
          <dt>{{ fieldLabel(item.name) }}</dt>
          <dd>{{ item.text }}</dd>
        </div>
      </dl>
      <span v-if="period" class="muted small period">
        {{ t("search.fields.temporal") }}: {{ period }}
      </span>
      <span v-if="date" class="muted small" :title="absoluteTime(date)">
        {{ t("search.updated", { when: relativeTime(date) }) }}
      </span>
      <span v-if="hit.completeness < 0.4" class="sparse small" :title="t('search.sparseHelp')">
        {{ t("search.sparse") }}
      </span>
      <details v-if="hit.duplicateCount" class="copies small">
        <summary :title="t('search.copiesTitle')">
          {{ t("search.copies", hit.duplicateCount) }}
        </summary>
        <ul>
          <li v-for="copy in hit.duplicates ?? []" :key="copy.id">
            <router-link :to="{ name: 'resource', params: { id: copy.id } }">{{
              copy.fdp.title
            }}</router-link>
          </li>
        </ul>
      </details>
      <a
        v-if="source"
        class="small source"
        :href="source"
        target="_blank"
        rel="noopener noreferrer"
      >
        {{ t("search.openSource") }} ↗
      </a>
    </footer>
  </article>
</template>

<style scoped>
.result {
  position: relative;
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-left: 4px solid var(--kind-accent, var(--fair-border));
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  display: grid;
  gap: var(--fair-space-2);
  min-width: 0;
}
.kind-dataset,
.kind-series {
  --kind-accent: var(--tool-accent);
}
.kind-catalog {
  --kind-accent: #8a6bd1;
}
.kind-service {
  --kind-accent: #2f9e8f;
}
.kind-fdp {
  --kind-accent: #d08a2e;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fair-space-1) var(--fair-space-2);
  font-size: var(--fair-text-sm);
  color: var(--fair-text-muted);
  min-width: 0;
}
.kind {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: var(--fair-text-xs, 0.72rem);
  color: var(--kind-accent, var(--fair-text-muted));
}
.meta a {
  color: inherit;
  overflow-wrap: anywhere;
  /* WCAG 2.5.8: a 24px target, as the links sit side by side. */
  display: inline-flex;
  align-items: center;
  min-height: 24px;
}
.sep {
  opacity: 0.6;
}
h3 {
  margin: 0;
  font-size: 1.08rem;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
h3 a {
  color: var(--fair-text);
  text-decoration: none;
}
h3 a:hover {
  color: var(--tool-accent);
  text-decoration: underline;
}
.description {
  margin: 0;
  color: var(--fair-text-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.chips {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag {
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-pill);
  background: transparent;
  color: var(--fair-text);
  padding: 2px 10px;
  font: inherit;
  font-size: var(--fair-text-xs, 0.75rem);
  cursor: pointer;
}
.tag.theme {
  background: var(--tool-accent-tint);
  border-color: transparent;
}
.tag.subject {
  background: var(--fair-surface-2, transparent);
  border-color: var(--tool-accent-tint);
}
.tag:hover {
  border-color: var(--tool-accent);
}
.formats {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--fair-text-sm);
}
.format {
  font-family: var(--fair-font-mono, monospace);
  font-size: var(--fair-text-xs, 0.75rem);
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.foot {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--fair-space-1) var(--fair-space-4);
  padding-top: var(--fair-space-2);
  border-top: 1px dashed var(--fair-border);
}
.keys {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fair-space-1) var(--fair-space-4);
  margin: 0;
  font-size: var(--fair-text-sm);
}
.keys div {
  display: flex;
  gap: 6px;
  min-width: 0;
}
.keys dt {
  color: var(--fair-text-muted);
}
.keys dt::after {
  content: ":";
}
.keys dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.sparse {
  color: var(--fair-warning, #b7791f);
}
.source {
  margin-left: auto;
}
.copies summary {
  cursor: pointer;
  color: var(--tool-accent);
}
.copies ul {
  margin: var(--fair-space-1) 0 0;
  padding-left: 1.2em;
}
mark {
  background: var(--tool-accent-tint);
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
}
</style>
