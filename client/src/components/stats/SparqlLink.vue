<script setup lang="ts">
/**
 * "Open as SPARQL query": the same question asked of the SPARQL endpoint, so
 * anyone can check and rework a chart (TASKS 15.1). Shown only when SPARQL is
 * on. The query is opened in the editor, not run.
 */
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useServiceInfo } from "@/composables/queries";
import { encodeQuery } from "@/sparql/history";

const props = defineProps<{ query: string }>();
const { t } = useI18n();
const info = useServiceInfo();
const encoded = ref<string | null>(null);

watch(
  () => props.query,
  (query) => {
    void encodeQuery(query).then((value) => (encoded.value = value));
  },
  { immediate: true },
);
</script>

<template>
  <router-link
    v-if="info.data.value?.sparql && encoded"
    class="sparql-link small"
    :to="{ name: 'sparql', query: { q: encoded } }"
    >{{ t("stats.openSparql") }}</router-link
  >
</template>
