<script setup lang="ts">
/**
 * A chart of a SELECT result with one category column and one number column.
 * Loaded on demand (Chart.js stays out of the main bundle). Colours come from
 * the theme tokens; the canvas carries a text summary for screen readers, and
 * the table remains the accessible view.
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { Bar, Line, Pie } from "vue-chartjs";
import { shortIri, type ChartShape, type Table } from "@/sparql/results";

ChartJS.register(
  ArcElement,
  BarElement,
  CategoryScale,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
);

const props = defineProps<{
  table: Table;
  shape: ChartShape;
  type: string;
  graphTitles: Record<string, string>;
}>();

const { t } = useI18n();
const KINDS = ["bar", "barh", "pie", "line"];
const kind = ref<string>(KINDS.includes(props.type) ? props.type : "bar");
// A new question brings its own default chart.
watch(
  () => props.type,
  (type) => {
    kind.value = KINDS.includes(type) ? type : "bar";
  },
);

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const labels = computed(() =>
  props.table.rows.map((row) => {
    const cell = row[props.shape.label];
    if (!cell) return "—";
    if (cell.term?.type === "uri") return props.graphTitles[cell.text] ?? shortIri(cell.text);
    return cell.text || "—";
  }),
);
const values = computed(() =>
  props.table.rows.map((row) => Number(row[props.shape.value]?.text ?? 0)),
);
const valueName = computed(() => props.table.columns[props.shape.value] ?? "");

const palette = computed(() => {
  const accent = token("--tool-accent", "#2a6fdb");
  const extra = [
    "--fair-success",
    "--fair-warning",
    "--fair-info",
    "--fair-danger",
    "--fair-node",
  ].map((n) => token(n, "#888"));
  return [accent, ...extra];
});

const data = computed(() => ({
  labels: labels.value,
  datasets: [
    {
      label: valueName.value,
      data: values.value,
      backgroundColor:
        kind.value === "pie"
          ? labels.value.map((_, i) => palette.value[i % palette.value.length])
          : palette.value[0],
      borderColor: palette.value[0],
    },
  ],
}));

const options = computed(() => {
  const text = token("--fair-text", "#333");
  const grid = token("--fair-separator", "#ddd");
  const axes = {
    x: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: true },
    y: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: true },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: kind.value === "barh" ? ("y" as const) : ("x" as const),
    plugins: { legend: { display: kind.value === "pie", labels: { color: text } } },
    scales: kind.value === "pie" ? {} : axes,
  };
});

const summary = computed(() =>
  t("sparql.chart.summary", {
    value: valueName.value,
    items: labels.value
      .slice(0, 10)
      .map((label, i) => `${label}: ${values.value[i]}`)
      .join("; "),
  }),
);
const height = computed(() =>
  kind.value === "barh" ? `${Math.max(240, labels.value.length * 26 + 60)}px` : "340px",
);
</script>

<template>
  <div class="chart">
    <div class="row kinds" role="radiogroup" :aria-label="t('sparql.chart.kind')">
      <label v-for="k in ['bar', 'barh', 'pie', 'line']" :key="k" class="small">
        <input v-model="kind" type="radio" :value="k" />
        {{ t(`sparql.chart.kinds.${k}`) }}
      </label>
    </div>
    <div class="canvas" :style="{ height }" role="img" :aria-label="summary">
      <Pie v-if="kind === 'pie'" :data="data" :options="options" />
      <Line v-else-if="kind === 'line'" :data="data" :options="options" />
      <Bar v-else :data="data" :options="options" />
    </div>
  </div>
</template>

<style scoped>
.chart {
  display: grid;
  gap: var(--fair-space-3);
}
.kinds {
  gap: var(--fair-space-3);
  flex-wrap: wrap;
}
.canvas {
  position: relative;
  width: 100%;
}
</style>
