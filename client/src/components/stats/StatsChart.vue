<script setup lang="ts">
/**
 * A chart on the Statistics page (TASKS 15.1): columns, horizontal bars or
 * lines over shared labels. One hue for magnitude; a second line, when there
 * is one, is context (muted, dashed), so the accent series is the point.
 *
 * Marks follow the dataviz specs: bars at most 24px thick with a 4px rounded
 * data end, 2px lines with gaps where no value exists, hairline solid grid,
 * a crosshair tooltip on lines and per-bar tooltips on bars, values at the end
 * of horizontal bars. Colours come from the theme tokens and follow the theme.
 * The table view under each chart carries every value.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type Plugin,
} from "chart.js";
import { Bar, Line } from "vue-chartjs";
import { darkTheme } from "@/composables/theme";
import { currentLocale } from "@/i18n";

ChartJS.register(
  BarElement,
  CategoryScale,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
);

export interface Series {
  label: string;
  data: (number | null)[];
  /** "accent" (default) is the point; "context" is drawn muted and dashed. */
  role?: "accent" | "context";
}

const props = withDefaults(
  defineProps<{
    kind: "column" | "barh" | "line";
    labels: string[];
    series: Series[];
    /** What the chart shows, for screen readers and the table's caption. */
    title: string;
    /** The first column's header in the table view. */
    labelHeader: string;
    height?: number;
  }>(),
  { height: 280 },
);

const { t } = useI18n();

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function number(value: number | null | undefined): string {
  return value == null ? "—" : new Intl.NumberFormat(currentLocale()).format(value);
}

function compact(value: number): string {
  if (Math.abs(value) < 10_000) return number(value);
  return new Intl.NumberFormat(currentLocale(), { notation: "compact" }).format(value);
}

// Read again when the theme changes: the tokens are the theme's.
const colours = computed(() => {
  void darkTheme.value;
  return {
    accent: token("--tool-accent", "#2563c7"),
    context: token("--fair-text-muted", "#5c6673"),
    text: token("--fair-text-muted", "#5c6673"),
    grid: token("--fair-separator", "#e8ecf1"),
    surface: token("--fair-surface", "#ffffff"),
  };
});

const data = computed(() => ({
  labels: props.labels,
  datasets: props.series.map((s) => {
    const colour = s.role === "context" ? colours.value.context : colours.value.accent;
    if (props.kind === "line") {
      return {
        label: s.label,
        data: s.data,
        borderColor: colour,
        backgroundColor: colour,
        borderWidth: 2,
        borderDash: s.role === "context" ? [5, 4] : [],
        borderCapStyle: "round" as const,
        borderJoinStyle: "round" as const,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: colours.value.surface,
        pointHitRadius: 10,
        spanGaps: false,
        tension: 0,
      };
    }
    return {
      label: s.label,
      data: s.data,
      backgroundColor: colour,
      hoverBackgroundColor: colour,
      borderRadius: 4,
      borderSkipped: "start" as const,
      maxBarThickness: 24,
    };
  }),
}));

// Horizontal bars carry their value at the tip, in text ink.
const valueLabels: Plugin<"bar"> = {
  id: "statsValueLabels",
  afterDatasetsDraw(chart) {
    if (props.kind !== "barh") return;
    const { ctx } = chart;
    ctx.save();
    ctx.fillStyle = token("--fair-text", "#384049");
    ctx.font = `12px ${token("--fair-font-sans", "system-ui, sans-serif")}`;
    ctx.textBaseline = "middle";
    chart.getDatasetMeta(0).data.forEach((bar, i) => {
      const value = props.series[0]?.data[i];
      if (value == null) return;
      const { x, y } = bar.getProps(["x", "y"], true) as { x: number; y: number };
      ctx.fillText(number(value), x + 6, y);
    });
    ctx.restore();
  },
};

const options = computed(() => {
  const c = colours.value;
  const horizontal = props.kind === "barh";
  const valueAxis = {
    beginAtZero: true,
    grid: { color: c.grid, lineWidth: 1 },
    border: { display: false },
    ticks: {
      color: c.text,
      precision: 0,
      callback: (v: string | number) => compact(Number(v)),
    },
    // Room for the value at a horizontal bar's tip.
    grace: horizontal ? "12%" : 0,
  };
  const labelAxis = {
    grid: { display: false },
    border: { color: c.grid },
    ticks: { color: c.text, autoSkip: true, maxRotation: 0, autoSkipPadding: 12 },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false as const,
    indexAxis: horizontal ? ("y" as const) : ("x" as const),
    interaction:
      props.kind === "line"
        ? { mode: "index" as const, intersect: false }
        : { mode: "nearest" as const, intersect: true },
    plugins: {
      legend: {
        display: props.series.length > 1,
        position: "top" as const,
        align: "start" as const,
        labels: { color: c.text, usePointStyle: true, pointStyle: "line", boxWidth: 24 },
      },
      tooltip: {
        callbacks: {
          label: (item: { dataset: { label?: string | undefined }; raw: unknown }) =>
            `${number(item.raw as number | null)}  ${item.dataset.label ?? ""}`,
        },
      },
    },
    scales: horizontal ? { x: valueAxis, y: labelAxis } : { x: labelAxis, y: valueAxis },
  };
});

const height = computed(() =>
  props.kind === "barh" ? Math.max(120, props.labels.length * 34 + 40) : props.height,
);

const summary = computed(() => {
  const first = props.series[0];
  if (!first) return props.title;
  const items = props.labels
    .map((label, i) => `${label}: ${number(first.data[i])}`)
    .filter((_, i) => first.data[i] != null);
  const shown = props.kind === "line" ? [items[0], items[items.length - 1]] : items.slice(0, 12);
  return `${props.title}. ${shown.filter(Boolean).join("; ")}`;
});
</script>

<template>
  <figure class="stats-chart">
    <div class="canvas" :style="{ height: `${height}px` }" role="img" :aria-label="summary">
      <Line v-if="kind === 'line'" :data="data" :options="options" />
      <Bar v-else :data="data" :options="options" :plugins="[valueLabels]" />
    </div>
    <details class="table-view">
      <summary>{{ t("stats.showTable") }}</summary>
      <div class="scroll">
        <table class="table">
          <caption class="visually-hidden">
            {{
              title
            }}
          </caption>
          <thead>
            <tr>
              <th scope="col">{{ labelHeader }}</th>
              <th v-for="s in series" :key="s.label" scope="col" class="num">{{ s.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(label, i) in labels" :key="label">
              <th scope="row">{{ label }}</th>
              <td v-for="s in series" :key="s.label" class="num">{{ number(s.data[i]) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </figure>
</template>

<style scoped>
.stats-chart {
  margin: 0;
  display: grid;
  gap: var(--fair-space-2);
  min-width: 0;
}
.canvas {
  position: relative;
  min-width: 0;
}
.table-view summary {
  cursor: pointer;
  color: var(--fair-text-muted);
  font-size: var(--fair-text-sm);
}
.scroll {
  max-height: 320px;
  overflow: auto;
  margin-top: var(--fair-space-2);
}
.num {
  text-align: end;
  font-variant-numeric: tabular-nums;
}
</style>
