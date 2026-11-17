<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { api } from "@/api";
import { completeOidc } from "@/auth/session";

const { t } = useI18n();
const router = useRouter();
const error = ref("");

onMounted(async () => {
  try {
    const target = await completeOidc(await api.config());
    void router.replace(target);
  } catch (err) {
    error.value = (err as Error).message;
  }
});
</script>

<template>
  <div class="page">
    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else class="muted">{{ t("signIn.completing") }}</p>
  </div>
</template>
