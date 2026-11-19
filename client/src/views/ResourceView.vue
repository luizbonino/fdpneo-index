<script setup lang="ts">
/**
 * One harvested resource (search design §6, TASKS 6.14): every value with
 * its label, the chain FDP › catalog › dataset, what it contains, the triples
 * as harvested (Turtle, JSON-LD, N-Triples), and ways onward: the source, the
 * SPARQL page, and searches for similar resources.
 */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { toast } from "@/composables/feedback";
import { LOCAL_FIELDS, RDF_FORMATS, searchApi, type ValueView } from "@/search/api";
import { encodeQuery } from "@/sparql/history";
import { isLinkable } from "@/sparql/results";
import StateBadge from "@/components/StateBadge.vue";
import RecordConformance from "@/components/conformance/RecordConformance.vue";
import { absoluteTime, formatPeriod, relativeTime } from "@/utils/format";

const props = defineProps<{ id: string }>();
const { t } = useI18n();
const router = useRouter();

const lang = ref<string>("");
const resource = useQuery({
  queryKey: computed(() => ["search-resource", props.id, lang.value]),
  queryFn: () => searchApi.resource(props.id, lang.value || undefined),
  retry: false,
});
const landing = useQuery({
  queryKey: ["search-landing"],
  queryFn: searchApi.landing,
  staleTime: 60_000,
});
const r = computed(() => resource.data.value);
const notFound = computed(
  () => resource.error.value instanceof ApiError && resource.error.value.status === 404,
);
watch(
  () => props.id,
  () => {
    lang.value = "";
    showTriples.value = false;
  },
);

const languages = computed(() =>
  Object.keys({ ...(r.value?.titles ?? {}), ...(r.value?.descriptions ?? {}) }).sort(),
);
const facetNames = computed(() => new Set((landing.data.value?.facets ?? []).map((f) => f.name)));

function fieldLabel(name: string): string {
  if (LOCAL_FIELDS.has(name)) return t(`search.fields.${name}`);
  const label = landing.data.value?.fields[name]?.label;
  return label?.en ?? Object.values(label ?? {})[0] ?? name;
}

/** Values in the profile's field order; fields the profile does not know come last. */
const rows = computed(() => {
  const values = r.value?.values ?? {};
  const order = Object.keys(landing.data.value?.fields ?? {});
  const names = Object.keys(values).sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b);
  });
  return names.map((name) => ({ name, values: values[name] ?? [] }));
});

function searchFor(name: string, value: ValueView) {
  return { name: "search", query: { [name]: value.value } };
}

const similar = computed(() => {
  const out: { label: string; to: ReturnType<typeof searchFor> }[] = [];
  for (const name of ["publisher", "theme", "keyword"]) {
    for (const value of (r.value?.values[name] ?? []).slice(0, 2)) {
      out.push({ label: `${fieldLabel(name)}: ${value.label}`, to: searchFor(name, value) });
    }
  }
  return out;
});

const showTriples = ref(false);
const triples = useQuery({
  queryKey: computed(() => ["search-triples", props.id]),
  queryFn: () => searchApi.triples(props.id, "ttl"),
  enabled: showTriples,
});

async function download(format: (typeof RDF_FORMATS)[number]) {
  try {
    const text = await searchApi.triples(props.id, format.key);
    const url = URL.createObjectURL(new Blob([text], { type: format.type }));
    const link = document.createElement("a");
    link.href = url;
    const tail =
      (r.value?.iri ?? "resource")
        .replace(/[/#]+$/, "")
        .split(/[/#]/)
        .pop() || "resource";
    link.download = `${tail.replace(/[^\w.-]+/g, "_")}.${format.ext}`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast((err as Error).message, "error");
  }
}

async function querySparql() {
  if (!r.value) return;
  const q = await encodeQuery(`DESCRIBE <${r.value.iri}>`);
  void router.push({ name: "sparql", query: { tab: "editor", q } });
}

const source = computed(() =>
  r.value && isLinkable(r.value.sourceUrl) ? r.value.sourceUrl : null,
);
const landingPage = computed(() =>
  r.value?.landingPage && isLinkable(r.value.landingPage) ? r.value.landingPage : null,
);
const completeness = computed(() => Math.round((r.value?.completeness ?? 0) * 100));
</script>

<template>
  <div class="page resource-page">
    <div v-if="resource.isLoading.value" class="muted">{{ t("common.loading") }}</div>
    <section v-else-if="notFound" class="card empty">
      <h1>{{ t("resource.notFound") }}</h1>
      <router-link class="btn" :to="{ name: 'search' }">{{ t("resource.back") }}</router-link>
    </section>
    <div v-else-if="resource.isError.value" class="notice error">
      {{ (resource.error.value as Error).message }}
      <button class="btn sm" @click="resource.refetch()">{{ t("common.retry") }}</button>
    </div>

    <template v-else-if="r">
      <nav class="crumbs" :aria-label="t('resource.path')">
        <router-link :to="{ name: 'search' }">{{ t("nav.search") }}</router-link>
        <span aria-hidden="true">›</span>
        <router-link :to="{ name: 'entry', params: { uuid: r.fdp.uuid } }">{{
          r.fdp.title
        }}</router-link>
        <template v-for="parent in r.parents.filter((p) => p.type !== 'fdp')" :key="parent.id">
          <span aria-hidden="true">›</span>
          <router-link :to="{ name: 'resource', params: { id: parent.id } }">{{
            parent.title
          }}</router-link>
        </template>
      </nav>

      <header class="page-head head">
        <div>
          <p class="eyebrow">{{ t(`search.kinds.${r.type}`) }}</p>
          <h1 :lang="r.titleLang || undefined">{{ r.title }}</h1>
          <p class="muted small fdp">
            {{ t("resource.from") }}
            <router-link :to="{ name: 'entry', params: { uuid: r.fdp.uuid } }">{{
              r.fdp.title
            }}</router-link>
            <StateBadge :state="r.fdp.state" />
          </p>
        </div>
        <label v-if="languages.length > 1" class="row">
          <span class="muted small">{{ t("resource.language") }}</span>
          <select v-model="lang" class="input select">
            <option value="">{{ t("resource.languageAuto") }}</option>
            <option v-for="code in languages" :key="code" :value="code">{{ code || "—" }}</option>
          </select>
        </label>
      </header>

      <p v-if="r.description" class="description">{{ r.description }}</p>

      <div class="actions row">
        <a v-if="source" class="btn" :href="source" target="_blank" rel="noopener noreferrer">
          {{ t("search.openSource") }} ↗
        </a>
        <a
          v-if="landingPage && landingPage !== source"
          class="btn"
          :href="landingPage"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ t("search.fields.landingPage") }} ↗
        </a>
        <button type="button" class="btn" @click="querySparql">{{ t("resource.sparql") }}</button>
        <span class="downloads row">
          <span class="muted small">{{ t("resource.download") }}</span>
          <button
            v-for="format in RDF_FORMATS"
            :key="format.key"
            type="button"
            class="btn ghost sm"
            @click="download(format)"
          >
            {{ format.label }}
          </button>
        </span>
      </div>

      <div class="grid">
        <section class="card">
          <h2>{{ t("resource.metadata") }}</h2>
          <dl class="facts values">
            <dt>{{ t("resource.iri") }}</dt>
            <dd class="mono small iri">{{ r.iri }}</dd>
            <template v-for="row in rows" :key="row.name">
              <dt>{{ fieldLabel(row.name) }}</dt>
              <dd>
                <ul class="value-list">
                  <li v-for="value in row.values" :key="value.value">
                    <router-link
                      v-if="facetNames.has(row.name)"
                      :to="searchFor(row.name, value)"
                      :title="t('resource.searchValue')"
                      >{{ value.label }}</router-link
                    >
                    <a
                      v-else-if="isLinkable(value.value)"
                      :href="value.value"
                      target="_blank"
                      rel="noopener noreferrer"
                      >{{ value.label }}</a
                    >
                    <span v-else>{{ value.label }}</span>
                    <span
                      v-if="value.label !== value.value && isLinkable(value.value)"
                      class="mono tiny muted"
                    >
                      {{ value.value }}
                    </span>
                    <span v-if="value.lang" class="lang">{{ value.lang }}</span>
                  </li>
                </ul>
              </dd>
            </template>
          </dl>
        </section>

        <aside class="stack side">
          <section class="card">
            <h2>{{ t("resource.record") }}</h2>
            <dl class="facts">
              <template v-if="r.issued">
                <dt>{{ t("search.fields.issued") }}</dt>
                <dd :title="absoluteTime(r.issued)">{{ relativeTime(r.issued) }}</dd>
              </template>
              <template v-if="r.modified">
                <dt>{{ t("search.fields.modified") }}</dt>
                <dd :title="absoluteTime(r.modified)">{{ relativeTime(r.modified) }}</dd>
              </template>
              <template v-if="r.bbox">
                <dt>{{ t("search.area.legend") }}</dt>
                <dd class="mono small">
                  {{ r.bbox.map((v) => v.toFixed(2)).join(", ") }}
                </dd>
              </template>
              <template v-if="r.temporal">
                <dt>{{ t("search.fields.temporal") }}</dt>
                <dd>{{ formatPeriod(r.temporal, t) }}</dd>
              </template>
              <dt>{{ t("resource.harvested") }}</dt>
              <dd :title="absoluteTime(r.harvestedAt)">{{ relativeTime(r.harvestedAt) }}</dd>
              <dt>{{ t("resource.completeness") }}</dt>
              <dd>
                <meter min="0" max="100" low="40" high="75" optimum="100" :value="completeness">
                  {{ completeness }}%
                </meter>
                <span class="small"> {{ completeness }}%</span>
              </dd>
            </dl>
          </section>

          <section v-if="r.children.length" class="card">
            <h2>{{ t("resource.contains", r.childCount) }}</h2>
            <ul class="children">
              <li v-for="child in r.children" :key="child.id">
                <span class="kind">{{ t(`search.kinds.${child.type}`) }}</span>
                <router-link :to="{ name: 'resource', params: { id: child.id } }">{{
                  child.title
                }}</router-link>
              </li>
            </ul>
          </section>

          <section v-if="r.duplicates?.length" class="card">
            <h2>{{ t("resource.copies") }}</h2>
            <p class="muted small">{{ t("resource.copiesLead") }}</p>
            <ul class="children">
              <li v-for="copy in r.duplicates" :key="copy.id">
                <router-link :to="{ name: 'resource', params: { id: copy.id } }">{{
                  copy.title
                }}</router-link>
                <span class="muted small"> · {{ copy.fdp.title }}</span>
                <StateBadge
                  v-if="!['ACTIVE', 'UNKNOWN'].includes(copy.fdp.state)"
                  :state="copy.fdp.state"
                />
              </li>
            </ul>
          </section>

          <section v-if="similar.length" class="card">
            <h2>{{ t("resource.similar") }}</h2>
            <ul class="similar">
              <li v-for="item in similar" :key="item.label">
                <router-link :to="item.to">{{ item.label }}</router-link>
              </li>
            </ul>
          </section>
        </aside>
      </div>

      <RecordConformance :id="id" :iri="r.iri" />

      <section class="card">
        <details @toggle="showTriples = ($event.target as HTMLDetailsElement).open">
          <summary>
            <h2>{{ t("resource.triples") }}</h2>
          </summary>
          <p v-if="triples.isLoading.value" class="muted">{{ t("common.loading") }}</p>
          <pre v-else-if="triples.data.value" class="code">{{ triples.data.value }}</pre>
        </details>
      </section>
    </template>
  </div>
</template>

<style scoped>
.crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: var(--fair-text-sm);
  color: var(--fair-text-muted);
  margin-bottom: var(--fair-space-3);
}
.crumbs a {
  color: inherit;
  overflow-wrap: anywhere;
}
.head {
  align-items: flex-start;
}
.head h1 {
  overflow-wrap: anywhere;
}
.fdp {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.description {
  max-width: 78ch;
  line-height: 1.6;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.actions {
  flex-wrap: wrap;
  margin: var(--fair-space-3) 0 var(--fair-space-5);
}
.downloads {
  flex-wrap: wrap;
  gap: 4px;
}
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: var(--fair-space-5);
  align-items: start;
  margin-bottom: var(--fair-space-5);
}
.side {
  display: grid;
  gap: var(--fair-space-4);
}
.values dd {
  min-width: 0;
}
.iri {
  overflow-wrap: anywhere;
}
.value-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}
.value-list li {
  overflow-wrap: anywhere;
}
.tiny {
  display: block;
  font-size: 0.72rem;
}
.lang {
  margin-left: 6px;
  font-size: 0.72rem;
  padding: 0 5px;
  border: 1px solid var(--fair-border);
  border-radius: 4px;
  color: var(--fair-text-muted);
}
.children,
.similar {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fair-space-2);
  max-height: 420px;
  overflow-y: auto;
}
.children li {
  display: grid;
  gap: 2px;
  overflow-wrap: anywhere;
}
.children .kind {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--fair-text-muted);
}
details summary {
  cursor: pointer;
}
details summary h2 {
  display: inline;
  font-size: 1rem;
}
.code {
  max-height: 520px;
  overflow: auto;
  margin-top: var(--fair-space-3);
}
@media (max-width: 860px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
