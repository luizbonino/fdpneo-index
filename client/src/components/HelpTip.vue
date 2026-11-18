<script setup lang="ts">
/**
 * An "i" button with a short explanation in a popover: the StatusHelp pattern
 * for any label (facet groups, filters). Closed by Escape, a click outside or
 * scrolling; fixed-positioned and clamped to the viewport.
 */
import { nextTick, onBeforeUnmount, ref, useId } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{ title: string; text: string }>();

const GUTTER = 16;
const WIDTH = 320;
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
    :aria-label="t('common.about', { name: props.title })"
    :title="t('common.about', { name: props.title })"
    :aria-expanded="open"
    :aria-controls="id"
    @click="toggle"
  >
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
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
      class="help-tip"
      role="dialog"
      :aria-labelledby="`${id}-title`"
      tabindex="-1"
      :style="{
        top: `${position.top}px`,
        left: `${position.left}px`,
        width: `${position.width}px`,
      }"
    >
      <h2 :id="`${id}-title`">{{ props.title }}</h2>
      <p>{{ props.text }}</p>
    </section>
  </Teleport>
</template>

<style scoped>
.info {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
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
.help-tip {
  position: fixed;
  z-index: 60;
  padding: var(--fair-space-3) var(--fair-space-4);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
  text-align: left;
}
.help-tip:focus {
  outline: none;
}
h2 {
  margin: 0 0 var(--fair-space-1);
  font-size: var(--fair-text-sm);
}
p {
  margin: 0;
  font-size: var(--fair-text-sm);
  line-height: 1.45;
}
</style>
