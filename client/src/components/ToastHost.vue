<script setup lang="ts">
import { dismiss, toastList } from "@/composables/feedback";
</script>

<template>
  <div class="toasts" role="status" aria-live="polite">
    <div v-for="item in toastList" :key="item.id" :class="['toast', item.tone]">
      <span>{{ item.message }}</span>
      <button class="btn ghost sm" :aria-label="$t('common.close')" @click="dismiss(item.id)">
        ×
      </button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: var(--fair-space-6);
  bottom: var(--fair-space-6);
  display: grid;
  gap: var(--fair-space-2);
  z-index: 50;
  max-width: min(420px, calc(100vw - 32px));
}
.toast {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fair-space-3);
  padding: 10px 10px 10px 16px;
  border-radius: var(--fair-radius-md);
  background: var(--fair-ink-lighter);
  color: #fff;
  box-shadow: var(--fair-shadow-3);
}
.toast.error {
  background: var(--fair-danger);
}
.toast .btn {
  color: inherit;
}
</style>
