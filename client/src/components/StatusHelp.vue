<script setup lang="ts">
/**
 * An "i" button that explains the statuses in a small popover.
 *
 * Opened by click (or Enter / Space); closed by Escape, a click outside, the
 * close button, or scrolling. It is fixed-positioned under the button and
 * clamped to the viewport, so it never causes horizontal scroll on a phone.
 * `permits` adds the approval statuses, which only administrators see.
 */
import { nextTick, onBeforeUnmount, ref, useId } from "vue";
import { useI18n } from "vue-i18n";
import { ENTRY_STATES, type Permit } from "@/api";
import StateBadge from "@/components/StateBadge.vue";
import PermitBadge from "@/components/PermitBadge.vue";

withDefaults(defineProps<{ permits?: boolean }>(), { permits: false });

const PERMITS: Permit[] = ["ACCEPTED", "PENDING", "REJECTED"];
const GUTTER = 16;
const WIDTH = 380;

const { t } = useI18n();
const id = useId();
const open = ref(false);
const button = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();
const position = ref({ top: 0, left: 0, width: WIDTH });

function place() {
  const anchor = button.value?.getBoundingClientRect();
  if (!anchor) return;
  const width = Math.min(WIDTH, window.innerWidth - 2 * GUTTER);
  const left = Math.min(Math.max(anchor.left, GUTTER), window.innerWidth - width - GUTTER);
  position.value = { top: anchor.bottom + 8, left, width };
}

function onPointer(event: PointerEvent) {
  const target = event.target as Node;
  if (!panel.value?.contains(target) && !button.value?.contains(target)) hide(false);
}
function onKey(event: KeyboardEvent) {
  if (event.key === "Escape") hide(true);
}
function onScroll() {
  hide(false);
}

function listen(on: boolean) {
  const method = on ? "addEventListener" : "removeEventListener";
  document[method]("pointerdown", onPointer as EventListener, true);
  document[method]("keydown", onKey as EventListener);
  window[method]("scroll", onScroll, true);
  window[method]("resize", onScroll);
}

async function show() {
  place();
  open.value = true;
  listen(true);
  await nextTick();
  panel.value?.focus();
}

function hide(restoreFocus: boolean) {
  if (!open.value) return;
  open.value = false;
  listen(false);
  if (restoreFocus) button.value?.focus();
}

function toggle() {
  if (open.value) hide(true);
  else void show();
}

onBeforeUnmount(() => listen(false));
</script>

<template>
  <button
    ref="button"
    type="button"
    class="info"
    :aria-label="t('statusHelp.open')"
    :title="t('statusHelp.open')"
    :aria-expanded="open"
    :aria-controls="id"
    @click="toggle"
  >
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <circle cx="10" cy="10" r="8.6" fill="none" stroke="currentColor" stroke-width="1.6" />
      <circle cx="10" cy="6.2" r="1.15" fill="currentColor" />
      <path d="M10 9v5.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
    </svg>
  </button>
  <Teleport to="body">
    <section
      v-if="open"
      :id="id"
      ref="panel"
      class="status-help"
      role="dialog"
      :aria-labelledby="`${id}-title`"
      tabindex="-1"
      :style="{
        top: `${position.top}px`,
        left: `${position.left}px`,
        width: `${position.width}px`,
      }"
    >
      <header class="head">
        <h2 :id="`${id}-title`">{{ t("statusHelp.title") }}</h2>
        <button type="button" class="btn ghost sm" @click="hide(true)">
          {{ t("common.close") }}
        </button>
      </header>
      <p class="muted small">{{ t("statusHelp.lead") }}</p>
      <dl class="list">
        <template v-for="state in ENTRY_STATES" :key="state">
          <dt><StateBadge :state="state" /></dt>
          <dd>{{ t(`state.help.${state}`) }}</dd>
        </template>
      </dl>
      <template v-if="permits">
        <h3>{{ t("statusHelp.permitsTitle") }}</h3>
        <dl class="list">
          <template v-for="permit in PERMITS" :key="permit">
            <dt><PermitBadge :permit="permit" /></dt>
            <dd>{{ t(`permit.help.${permit}`) }}</dd>
          </template>
        </dl>
      </template>
    </section>
  </Teleport>
</template>

<style scoped>
.info {
  display: inline-grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: var(--fair-radius-pill);
  background: transparent;
  color: var(--fair-text-muted);
  cursor: pointer;
  flex: none;
}
.info:hover,
.info[aria-expanded="true"] {
  background: var(--tool-accent-tint);
  color: var(--tool-accent);
}
.status-help {
  position: fixed;
  z-index: 60;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
  text-align: left;
}
.status-help:focus {
  outline: none;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fair-space-2);
}
h2 {
  margin: 0;
  font-size: var(--fair-text-md, 1rem);
}
h3 {
  margin: var(--fair-space-4) 0 0;
  font-size: var(--fair-text-sm);
  color: var(--fair-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--fair-space-3) var(--fair-space-3);
  margin: var(--fair-space-3) 0 0;
  align-items: start;
}
dt {
  margin: 0;
}
dd {
  margin: 0;
  font-size: var(--fair-text-sm);
  line-height: 1.45;
}
@media (max-width: 420px) {
  .list {
    grid-template-columns: 1fr;
    gap: var(--fair-space-1);
  }
  dd {
    margin-bottom: var(--fair-space-2);
  }
}
</style>
