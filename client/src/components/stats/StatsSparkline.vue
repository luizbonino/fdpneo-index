<script setup lang="ts">
/**
 * A small line of one statistic over the days (TASKS 15.1), in the accent hue,
 * its own scale, no axes. The tile beside it gives the current value; the
 * tooltip gives the day's.
 */
import { computed } from "vue";
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { Line } from "vue-chartjs";
import { darkTheme } from "@/composables/theme";
import { currentLocale } from "@/i18n";

ChartJS.register(CategoryScale, LinearScale, LineElement, PointElement, Tooltip);

const props = defineProps<{ labels: string[]; data: (number | null)[]; title: string }>();

function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const chart = computed(() => {
  void darkTheme.value;
  const accent = token("--tool-accent", "#2563c7");
  return {
    data: {
      labels: props.labels,
      datasets: [
        {
          label: props.title,
          data: props.data,
          borderColor: accent,
          backgroundColor: accent,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: token("--fair-surface", "#ffffff"),
          pointHitRadius: 8,
          spanGaps: false,
          tension: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false as const,
      interaction: { mode: "index" as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          callbacks: {
            label: (item: { raw: unknown }) =>
              new Intl.NumberFormat(currentLocale()).format(Number(item.raw)),
          },
        },
      },
      scales: { x: { display: false }, y: { display: false, grace: "10%" } },
    },
  };
});
</script>

<template>
  <div class="sparkline" aria-hidden="true">
    <Line :data="chart.data" :options="chart.options" />
  </div>
</template>

<style scoped>
.sparkline {
  position: relative;
  height: 44px;
  min-width: 0;
}
</style>
