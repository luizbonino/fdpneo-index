<script setup lang="ts">
/**
 * Administrators: save the current search under a title (TASKS 10.3), and
 * optionally feature it on the search page for everyone.
 */
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQueryClient } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { toast } from "@/composables/feedback";
import { searchApi } from "@/search/api";

const props = defineProps<{ query: string; suggestion: string }>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const client = useQueryClient();

const dialog = ref<HTMLDialogElement>();
const title = ref(props.suggestion);
const description = ref("");
const featured = ref(false);
const error = ref("");
const saving = ref(false);

onMounted(() => dialog.value?.showModal());

async function save() {
  error.value = "";
  if (!title.value.trim()) {
    error.value = t("saved.titleNeeded");
    return;
  }
  saving.value = true;
  try {
    await searchApi.saveSearch({
      title: title.value,
      query: props.query,
      description: description.value || null,
      featured: featured.value,
    });
    await client.invalidateQueries({ queryKey: ["search-landing"] });
    await client.invalidateQueries({ queryKey: ["saved-searches"] });
    toast(t("saved.saved"));
    dialog.value?.close();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message;
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <dialog
    ref="dialog"
    class="save-dialog"
    aria-labelledby="save-title"
    @close="emit('close')"
    @cancel="emit('close')"
  >
    <form class="stack" @submit.prevent="save">
      <h2 id="save-title">{{ t("saved.dialogTitle") }}</h2>
      <div class="field">
        <label for="saved-title">{{ t("saved.title") }}</label>
        <input
          id="saved-title"
          v-model="title"
          class="input"
          maxlength="120"
          :aria-invalid="!!error || undefined"
          aria-describedby="saved-error"
        />
      </div>
      <div class="field">
        <label for="saved-description">{{ t("saved.description") }}</label>
        <input id="saved-description" v-model="description" class="input" maxlength="500" />
      </div>
      <label class="check">
        <input v-model="featured" type="checkbox" />
        {{ t("saved.feature") }}
      </label>
      <p class="muted small mono query">{{ query || t("saved.everything") }}</p>
      <span id="saved-error" class="error" role="alert">{{ error }}</span>
      <div class="row">
        <button type="button" class="btn ghost" @click="dialog?.close()">
          {{ t("common.cancel") }}
        </button>
        <span class="spacer" />
        <button type="submit" class="btn primary" :disabled="saving">
          {{ t("saved.save") }}
        </button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
.save-dialog {
  width: min(520px, calc(100vw - 32px));
  padding: var(--fair-space-4) var(--fair-space-5);
  border: 1px solid var(--fair-border);
  border-radius: var(--fair-radius-lg);
  background: var(--fair-surface);
  color: var(--fair-text);
  box-shadow: var(--fair-shadow-4);
}
.save-dialog::backdrop {
  background: rgb(0 0 0 / 0.35);
}
h2 {
  margin: 0;
  font-size: var(--fair-text-md, 1rem);
}
.query {
  overflow-wrap: anywhere;
  margin: 0;
}
.spacer {
  flex: 1;
}
.check {
  display: flex;
  gap: var(--fair-space-2);
  align-items: center;
}
</style>
