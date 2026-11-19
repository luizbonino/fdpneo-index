<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{ value: string; label?: string; multiline?: boolean }>();
const copied = ref(false);

async function copy() {
  try {
    await navigator.clipboard.writeText(props.value);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    copied.value = false;
  }
}
</script>

<template>
  <div class="copy-field">
    <pre v-if="multiline" class="code" :aria-label="label">{{ value }}</pre>
    <code v-else class="code inline" :aria-label="label">{{ value }}</code>
    <button class="btn sm" type="button" @click="copy">
      {{ copied ? $t("register.copied") : $t("register.copy") }}
    </button>
  </div>
</template>

<style scoped>
.copy-field {
  display: flex;
  gap: var(--fair-space-2);
  align-items: flex-start;
  /* A long command scrolls inside its box instead of widening the page. */
  min-width: 0;
  max-width: 100%;
}
.copy-field .code {
  flex: 1;
  min-width: 0;
}
.code.inline {
  display: block;
  padding: 8px 12px;
  white-space: nowrap;
}
</style>
