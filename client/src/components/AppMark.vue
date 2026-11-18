<script setup lang="ts">
/**
 * The FDPneo Index mark, in the FAIR Ecosystem family.
 *
 * The ecosystem's node-on-a-stem glyph (the FAIR Data Point mark) with its
 * stem turned 45° into a handle: a lens over a data point, the index that
 * finds FAIR Data Points. Ring and handle take `currentColor` (ink, so they
 * flip in dark mode); the node is the directory accent. The same geometry is
 * in docs/assets/fdpneo-index.svg and public/favicon.svg.
 */
withDefaults(defineProps<{ size?: number }>(), { size: 32 });
</script>

<template>
  <svg class="mark" :width="size" :height="size" viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <circle cx="27" cy="27" r="15" stroke="currentColor" stroke-width="3.2" />
    <line
      x1="38"
      y1="38"
      x2="51"
      y2="51"
      stroke="currentColor"
      stroke-width="4.4"
      stroke-linecap="round"
    />
    <circle cx="27" cy="27" r="6.6" fill="var(--tool-accent)" />
  </svg>
</template>

<style scoped>
.mark {
  flex: none;
  display: block;
  color: var(--fair-ink);
}
</style>
