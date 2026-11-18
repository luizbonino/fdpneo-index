<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { session, signInWithOidc, signInWithToken, safeReturnTo } from "@/auth/session";
import { useServiceInfo } from "@/composables/queries";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const info = useServiceInfo();

const token = ref("");
const error = ref("");
const busy = ref(false);

function returnTo(): string {
  return safeReturnTo(route.query.returnTo);
}

async function oidc() {
  if (info.data.value) await signInWithOidc(info.data.value, returnTo());
}

async function useToken() {
  busy.value = true;
  error.value = "";
  const ok = await signInWithToken(token.value);
  busy.value = false;
  if (ok) void router.replace(returnTo());
  else error.value = t("signIn.invalid");
}
</script>

<template>
  <div class="page narrow">
    <header>
      <h1>{{ t("signIn.title") }}</h1>
      <p class="lead muted">{{ t("signIn.lead") }}</p>
    </header>
    <p v-if="session.notAdmin" class="notice warn">{{ t("signIn.notAdmin") }}</p>
    <p v-if="session.ready && !session.oidcAvailable && !session.tokenAvailable" class="notice">
      {{ t("signIn.unavailable") }}
    </p>
    <section v-if="session.oidcAvailable" class="card stack">
      <button class="btn primary" @click="oidc">{{ t("signIn.oidc") }}</button>
    </section>
    <form v-if="session.tokenAvailable" class="card stack" @submit.prevent="useToken">
      <h2>{{ t("signIn.token") }}</h2>
      <div class="field">
        <label for="admin-token">{{ t("signIn.tokenLabel") }}</label>
        <input
          id="admin-token"
          v-model="token"
          class="input mono"
          type="password"
          autocomplete="off"
          required
          :aria-invalid="!!error"
          aria-describedby="admin-token-help"
        />
        <span id="admin-token-help" class="help">{{ t("signIn.tokenHelp") }}</span>
        <span v-if="error" class="error" role="alert">{{ error }}</span>
      </div>
      <div>
        <button class="btn primary" type="submit" :disabled="busy || !token.trim()">
          {{ t("signIn.submit") }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.narrow {
  max-width: 560px;
}
</style>
