<script setup lang="ts">
/**
 * One record's conformance (TASKS 11.3): per profile, its status and
 * findings. Hidden when there are no results or the reader may not see them.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { session } from "@/auth/session";
import { componentName, conformanceApi, packTitle, SEVERITY_TONE } from "@/conformance/api";
import { shortIri } from "@/sparql/results";
import { absoluteTime, relativeTime } from "@/utils/format";

const props = defineProps<{ id: string; iri: string }>();
const { t, locale } = useI18n();

const report = useQuery({
  queryKey: computed(() => ["conformance-record", props.id, session.isAdmin]),
  queryFn: () => conformanceApi.record(props.id),
  retry: false,
});
const packs = useQuery({ queryKey: ["conformance-packs"], queryFn: conformanceApi.packs });

/** What the record declares, from its declared result (",dcat-ap,profile,"). */
const declaredNames = computed(() => {
  const found = report.data.value?.results.find((r) => r.pack === "declared");
  return (found?.variant ?? "").split(",").filter(Boolean);
});
function variantText(result: { pack: string; variant?: string | null }): string {
  if (result.pack !== "declared") return result.variant ?? "";
  const names = declaredNames.value.map((n) =>
    n === "profile"
      ? t("conformance.declared.ownProfile")
      : packTitle(packs.data.value, n, locale.value),
  );
  return t("conformance.declared.names", { names: names.join(", ") });
}
function lensNote(pack: string): string {
  return declaredNames.value.includes(pack)
    ? t("conformance.lens.recordClaimed")
    : t("conformance.lens.recordNotClaimed");
}

const STATUS_TONE: Record<string, string> = {
  conforms: "ok",
  violations: "bad",
  error: "warn",
  undeclared: "neutral",
  unresolved: "warn",
};
</script>

<template>
  <section
    v-if="report.data.value?.results.length"
    id="conformance"
    class="card stack"
    aria-labelledby="record-conformance-h"
  >
    <h2 id="record-conformance-h">{{ t("conformance.heading") }}</h2>
    <p class="muted small">{{ t("conformance.recordLead") }}</p>
    <details
      v-for="result in report.data.value.results"
      :key="result.pack"
      :open="result.role !== 'lens' && !['conforms', 'undeclared'].includes(result.status)"
      class="pack"
    >
      <summary>
        <strong>{{ packTitle(packs.data.value, result.pack, locale) }}</strong>
        <span v-if="result.variant" class="muted small">({{ variantText(result) }})</span>
        <span v-if="result.role === 'lens'" class="muted small">{{ lensNote(result.pack) }}</span>
        <span :class="['chip', STATUS_TONE[result.status]]">
          {{ t(`conformance.status.${result.status}`) }}
        </span>
        <span class="muted small">
          {{ t("conformance.counts", { v: result.violations, w: result.warnings }) }}
          <template v-if="result.validatedAt">
            ·
            <span :title="absoluteTime(result.validatedAt)">
              {{ t("conformance.checked", { when: relativeTime(result.validatedAt) }) }}
            </span>
          </template>
        </span>
      </summary>
      <p v-if="result.error" class="small error">{{ result.error }}</p>
      <div v-if="result.findings.length" class="table-scroll">
        <table class="table findings">
          <thead>
            <tr>
              <th scope="col">{{ t("conformance.severityColumn") }}</th>
              <th scope="col">{{ t("conformance.property") }}</th>
              <th scope="col">{{ t("conformance.problem") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(finding, i) in result.findings" :key="i">
              <td>
                <span :class="['chip', SEVERITY_TONE[finding.severity]]">
                  {{ t(`conformance.severity.${finding.severity}`) }}
                </span>
              </td>
              <td>
                <code v-if="finding.path" :title="finding.path">{{ shortIri(finding.path) }}</code>
                <span v-else class="muted">—</span>
                <span
                  v-if="finding.focusNode !== iri"
                  class="muted small focus"
                  :title="finding.focusNode"
                >
                  {{
                    finding.focusNode.startsWith("_:")
                      ? t("conformance.onNested")
                      : t("conformance.on", { node: shortIri(finding.focusNode) })
                  }}
                </span>
              </td>
              <td>
                <span class="small">
                  {{
                    t(
                      `conformance.component.${componentName(finding.component)}`,
                      componentName(finding.component),
                    )
                  }}
                </span>
                <span v-if="finding.message" class="small message">{{ finding.message }}</span>
                <span v-if="finding.value" class="muted small message">
                  {{ t("conformance.value", { value: finding.value }) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="result.findings.length >= 50" class="muted small">
        {{ t("conformance.firstFifty") }}
      </p>
    </details>
  </section>
</template>

<style scoped>
h2 {
  margin: 0;
}
.pack {
  border-top: 1px solid var(--fair-separator);
  padding-top: var(--fair-space-3);
}
summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fair-space-2);
  cursor: pointer;
}
.table-scroll {
  overflow-x: auto;
}
.findings {
  width: 100%;
  margin-top: var(--fair-space-3);
}
.focus,
.message {
  display: block;
  overflow-wrap: anywhere;
}
.error {
  color: var(--fair-danger);
}
</style>
