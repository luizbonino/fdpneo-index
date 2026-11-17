<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { answer, confirmState } from "@/composables/feedback";

const dialog = ref<HTMLDialogElement | null>(null);

watch(
  () => confirmState.current,
  async (current) => {
    await nextTick();
    if (current && dialog.value && !dialog.value.open) dialog.value.showModal?.();
    if (!current && dialog.value?.open) dialog.value.close();
  },
);
</script>

<template>
  <dialog ref="dialog" class="confirm" @cancel.prevent="answer(false)">
    <template v-if="confirmState.current">
      <p>{{ confirmState.current.message }}</p>
      <div class="row actions">
        <button class="btn" @click="answer(false)">{{ $t("common.cancel") }}</button>
        <button
          :class="['btn', confirmState.current.danger ? 'danger solid' : 'primary']"
          autofocus
          @click="answer(true)"
        >
          {{ confirmState.current.confirmLabel }}
        </button>
      </div>
    </template>
  </dialog>
</template>

<style scoped>
.confirm {
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  padding: var(--fair-space-6);
  max-width: 480px;
  background: var(--fair-surface);
  color: var(--fair-text-strong);
  box-shadow: var(--fair-shadow-4);
}
.confirm::backdrop {
  background: rgba(16, 22, 30, 0.45);
}
.actions {
  justify-content: flex-end;
  margin-top: var(--fair-space-6);
}
</style>
