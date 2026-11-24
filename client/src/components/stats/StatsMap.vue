<script setup lang="ts">
/**
 * A map of counts by country (TASKS 15.4): a choropleth in one hue, light to
 * dark, on a log scale in at most five steps (counts are skewed: hundreds of
 * records in one country, a handful in the next); countries without any stay
 * neutral. Drawn as SVG from Natural Earth's 1:110m borders (world-atlas,
 * public domain), bundled: no tiles, no requests elsewhere. The view fits the
 * countries that have counts, with a minimum span, so a Europe-only index shows
 * Europe. Hover or keyboard focus shows a country's count; a country can link
 * to a search. Small countries the 1:110m borders leave out are in the table,
 * which the page shows under every map.
 */
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { useRouter, type RouteLocationRaw } from "vue-router";
import { useI18n } from "vue-i18n";
import { geoArea, geoBounds, geoNaturalEarth1, geoPath } from "d3-geo";
import type { Feature, FeatureCollection, Geometry, MultiPoint, Polygon } from "geojson";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import { currentLocale } from "@/i18n";
import { NUMERIC_TO_ALPHA2 } from "./countryCodes";

const props = defineProps<{
  /** Count per ISO 3166-1 alpha-2 code. */
  counts: Record<string, number>;
  /** What the map shows, for screen readers. */
  title: string;
  /** "{n} FDPs" and the like, for the tooltip and labels. */
  describe: (n: number) => string;
  /** Where a country leads, if anywhere. */
  link?: (country: string) => RouteLocationRaw | null;
}>();

const router = useRouter();
const { t } = useI18n();
const WIDTH = 960;
const HEIGHT = 500;

type Country = Feature<Geometry, { name: string }> & { code: string | null };
const countries = shallowRef<Country[]>([]);

onMounted(async () => {
  const topology = (await import("world-atlas/countries-110m.json"))
    .default as unknown as Topology<{
    countries: GeometryCollection<{ name: string }>;
  }>;
  const collection = feature(topology, topology.objects.countries) as FeatureCollection<
    Geometry,
    { name: string }
  >;
  countries.value = collection.features
    .filter((f) => f.id !== "010") // Antarctica: no FDPs, a third of the map
    .map((f) => ({ ...f, code: NUMERIC_TO_ALPHA2[String(f.id)] ?? null }));
});

const names = computed(() => {
  try {
    return new Intl.DisplayNames([currentLocale()], { type: "region" });
  } catch {
    return null;
  }
});
function nameOf(code: string, fallback = code): string {
  return names.value?.of(code) ?? fallback;
}

// --- the view: fitted to the countries with counts -------------------------------------

/** A country's largest landmass: French Guiana does not pull a map of France to America. */
function mainland(country: Country): Feature {
  const geometry = country.geometry;
  if (geometry.type !== "MultiPolygon") return country;
  const polygons: Polygon[] = geometry.coordinates.map((coordinates) => ({
    type: "Polygon",
    coordinates,
  }));
  const largest = polygons.reduce((a, b) => (geoArea(b) > geoArea(a) ? b : a));
  return { type: "Feature", properties: {}, geometry: largest };
}

// Europe, for the "Europe" view: where most FDPs and most spatial coverage are today.
const EUROPE = new Set(
  (
    "AD AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GG GI GR HR HU IE IM IS IT JE " +
    "LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK"
  ).split(" "),
);
const total = computed(() => Object.values(props.counts).reduce((a, b) => a + b, 0));
const inEurope = computed(() =>
  Object.entries(props.counts).reduce((sum, [code, n]) => sum + (EUROPE.has(code) ? n : 0), 0),
);
/** Offer the choice only when there are counts on both sides of it. */
const regional = computed(() => inEurope.value > 0 && inEurope.value < total.value);
const region = ref<"all" | "europe">("all");
watch(
  [inEurope, total],
  () => {
    region.value = regional.value && inEurope.value * 2 >= total.value ? "europe" : "all";
  },
  { immediate: true },
);

const projection = computed(() => {
  const projection = geoNaturalEarth1();
  if (region.value === "europe" && regional.value) {
    const box: MultiPoint = {
      type: "MultiPoint",
      coordinates: [
        [-24, 35],
        [42, 35],
        [-24, 71],
        [42, 71],
      ],
    };
    return projection.fitExtent(
      [
        [12, 12],
        [WIDTH - 12, HEIGHT - 12],
      ],
      box,
    );
  }
  const withData = countries.value.filter((c) => c.code && (props.counts[c.code] ?? 0) > 0);
  const target = (withData.length ? withData : countries.value).map(mainland);
  if (!target.length) return projection.fitSize([WIDTH, HEIGHT], { type: "Sphere" });
  const [[west, south], [east, north]] = geoBounds({
    type: "FeatureCollection",
    features: target,
  });
  // Across the antimeridian, or the whole world: the whole world.
  if (east < west || east - west > 300) {
    return projection.fitExtent(
      [
        [8, 8],
        [WIDTH - 8, HEIGHT - 8],
      ],
      { type: "FeatureCollection", features: countries.value },
    );
  }
  // At least 40° by 22°, so one small country still shows its neighbours.
  const lon = (west + east) / 2;
  const lat = (south + north) / 2;
  const halfLon = Math.max((east - west) / 2 + 4, 20);
  const halfLat = Math.max((north - south) / 2 + 3, 11);
  const box: MultiPoint = {
    type: "MultiPoint",
    coordinates: [
      [lon - halfLon, Math.max(-85, lat - halfLat)],
      [lon + halfLon, Math.min(85, lat + halfLat)],
      [lon - halfLon, Math.min(85, lat + halfLat)],
      [lon + halfLon, Math.max(-85, lat - halfLat)],
    ],
  };
  return projection.fitExtent(
    [
      [12, 12],
      [WIDTH - 12, HEIGHT - 12],
    ],
    box,
  );
});
const path = computed(() => geoPath(projection.value));

// --- colour: one hue, five log-scale steps ----------------------------------------------

const max = computed(() => Math.max(0, ...Object.values(props.counts)));
const steps = computed(() => {
  const top = max.value;
  if (top <= 0) return [] as number[];
  if (top <= 5) return Array.from({ length: top }, (_, i) => i + 1);
  const bounds = new Set<number>();
  for (let i = 1; i <= 5; i++) bounds.add(Math.max(1, Math.round(top ** (i / 5))));
  return [...bounds].sort((a, b) => a - b);
});
/** Upper bounds of the steps; a count falls in the first step it does not exceed. */
function step(n: number): number {
  return steps.value.findIndex((upper) => n <= upper);
}
function fill(n: number): string {
  if (!n) return "var(--fair-separator)";
  const count = steps.value.length;
  const share = count <= 1 ? 100 : Math.round(35 + (65 * step(n)) / (count - 1));
  return `color-mix(in oklab, var(--tool-accent) ${share}%, var(--fair-surface))`;
}
const legend = computed(() =>
  steps.value.map((upper, i) => {
    const lower = i === 0 ? 1 : steps.value[i - 1]! + 1;
    const format = new Intl.NumberFormat(currentLocale());
    return {
      upper,
      label:
        lower === upper ? format.format(upper) : `${format.format(lower)}–${format.format(upper)}`,
    };
  }),
);

// --- what is drawn -----------------------------------------------------------------------

const shapes = computed(() =>
  countries.value.map((c) => {
    const count = c.code ? (props.counts[c.code] ?? 0) : 0;
    return {
      key: `${String(c.id)}-${c.properties.name}`,
      code: c.code,
      d: path.value(c) ?? "",
      count,
      label: c.code ? `${nameOf(c.code, c.properties.name)}: ${props.describe(count)}` : "",
      target: c.code && count > 0 && props.link ? props.link(c.code) : null,
    };
  }),
);

// --- hover and focus ----------------------------------------------------------------------

const box = ref<HTMLElement | null>(null);
const tip = ref<{ text: string; x: number; y: number } | null>(null);
function show(event: MouseEvent | FocusEvent, text: string) {
  const frame = box.value?.getBoundingClientRect();
  if (!frame || !text) return;
  if (event instanceof MouseEvent) {
    tip.value = { text, x: event.clientX - frame.left, y: event.clientY - frame.top };
  } else {
    const shape = (event.target as SVGElement).getBoundingClientRect();
    tip.value = {
      text,
      x: shape.left + shape.width / 2 - frame.left,
      y: shape.top + shape.height / 2 - frame.top,
    };
  }
}
function open(target: RouteLocationRaw | null) {
  if (target) void router.push(target);
}
</script>

<template>
  <figure class="stats-map">
    <div v-if="regional" class="row regions" role="radiogroup" :aria-label="t('stats.mapArea')">
      <label v-for="r in ['europe', 'all'] as const" :key="r" class="small">
        <input v-model="region" type="radio" :value="r" />
        {{ t(`stats.mapAreas.${r}`) }}
      </label>
    </div>
    <div ref="box" class="frame" @mouseleave="tip = null">
      <svg
        :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
        role="group"
        :aria-label="title"
        preserveAspectRatio="xMidYMid meet"
      >
        <g>
          <template v-for="s in shapes" :key="s.key">
            <path
              v-if="s.count > 0"
              :d="s.d"
              :style="{ fill: fill(s.count) }"
              class="country counted"
              :class="{ linked: s.target }"
              tabindex="0"
              :role="s.target ? 'link' : 'img'"
              :aria-label="s.label"
              @mousemove="show($event, s.label)"
              @focus="show($event, s.label)"
              @blur="tip = null"
              @click="open(s.target)"
              @keydown.enter="open(s.target)"
            />
            <path v-else :d="s.d" :style="{ fill: fill(0) }" class="country" aria-hidden="true" />
          </template>
        </g>
      </svg>
      <div v-if="tip" class="tip" role="status" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }">
        {{ tip.text }}
      </div>
    </div>
    <ul v-if="legend.length" class="legend small" aria-hidden="true">
      <li v-for="item in legend" :key="item.upper">
        <span class="swatch" :style="{ background: fill(item.upper) }" />{{ item.label }}
      </li>
    </ul>
  </figure>
</template>

<style scoped>
.stats-map {
  margin: 0;
  display: grid;
  gap: var(--fair-space-2);
  min-width: 0;
}
.frame {
  position: relative;
  min-width: 0;
  overflow: hidden;
}
.regions {
  gap: var(--fair-space-3);
}
.regions label {
  display: inline-flex;
  align-items: center;
  gap: var(--fair-space-1);
  min-height: 32px;
}
svg {
  display: block;
  width: 100%;
  height: auto;
}
.country {
  stroke: var(--fair-surface);
  stroke-width: 0.6;
  vector-effect: non-scaling-stroke;
}
.counted {
  outline: none;
}
.counted:hover,
.counted:focus-visible {
  stroke: var(--fair-text-strong);
  stroke-width: 1.5;
}
.linked {
  cursor: pointer;
}
.tip {
  position: absolute;
  transform: translate(-50%, calc(-100% - 10px));
  pointer-events: none;
  background: var(--fair-surface);
  color: var(--fair-text-strong);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-md);
  box-shadow: var(--fair-shadow-1);
  padding: 4px 8px;
  font-size: var(--fair-text-sm);
  white-space: nowrap;
}
.legend {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--fair-space-3);
  color: var(--fair-text-muted);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-variant-numeric: tabular-nums;
}
.swatch {
  width: 14px;
  height: 14px;
  border-radius: 3px;
}
</style>
