<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { useInvalidateEntries } from "@/composables/queries";
import { toast } from "@/composables/feedback";
import CopyField from "@/components/CopyField.vue";
import { runtimeApiUrl, runtimePublicOrigin } from "@/runtimeConfig";

const { t } = useI18n();
const invalidate = useInvalidateEntries();

// FDPs ping the API's root; by default the app and the API share an origin.
const endpoint = computed(() => `${runtimeApiUrl() || runtimePublicOrigin()}/`);
const fdpneo = computed(() => `FDP_INDEX_PING_TARGETS=${endpoint.value}`);
const java = computed(() => `ping:\n  endpoints:\n    - ${endpoint.value}`);
const curl = computed(
  () =>
    `curl -i -X POST ${endpoint.value} \\\n  -H 'Content-Type: application/json' \\\n  -d '{"clientUrl": "https://your-fdp.example.org"}'`,
);

const address = ref("");
const error = ref("");
const adding = ref(false);

async function add() {
  error.value = "";
  adding.value = true;
  try {
    await api.trigger(address.value.trim());
    toast(t("register.added", { url: address.value.trim() }));
    address.value = "";
    setTimeout(() => void invalidate(), 2500);
  } catch (err) {
    error.value =
      err instanceof ApiError ? (Object.values(err.fieldErrors)[0] ?? err.message) : String(err);
  } finally {
    adding.value = false;
  }
}
</script>

<template>
  <div class="page narrow">
    <header class="page-head">
      <div>
        <h1>{{ t("register.title") }}</h1>
        <p class="lead">{{ t("register.lead") }}</p>
      </div>
    </header>

    <section class="card stack">
      <span class="eyebrow">{{ t("register.endpoint") }}</span>
      <CopyField :value="endpoint" :label="t('register.endpoint')" />
    </section>

    <section class="card stack">
      <h2>{{ t("register.fdpneo") }}</h2>
      <p class="muted">{{ t("register.fdpneoHelp") }}</p>
      <CopyField :value="fdpneo" />
      <h2>{{ t("register.javaFdp") }}</h2>
      <p class="muted">{{ t("register.javaFdpHelp") }}</p>
      <CopyField :value="java" multiline />
      <h2>{{ t("register.curl") }}</h2>
      <p class="muted">{{ t("register.curlHelp") }}</p>
      <CopyField :value="curl" multiline />
    </section>

    <section class="card stack">
      <h2>{{ t("register.afterwards") }}</h2>
      <ol class="steps">
        <li>{{ t("register.steps.one") }}</li>
        <li>{{ t("register.steps.two") }}</li>
        <li>{{ t("register.steps.three") }}</li>
      </ol>
    </section>

    <section v-if="session.isAdmin" id="add" class="card stack">
      <h2>{{ t("register.adminTitle") }}</h2>
      <p class="muted">{{ t("register.adminHelp") }}</p>
      <form class="add" @submit.prevent="add">
        <div class="field">
          <label for="fdp-address">{{ t("register.addressLabel") }}</label>
          <input
            id="fdp-address"
            v-model="address"
            class="input"
            type="url"
            required
            placeholder="https://fdp.example.org"
            :aria-invalid="!!error"
            aria-describedby="fdp-address-error"
          />
          <span id="fdp-address-error" class="error" role="alert">{{ error }}</span>
        </div>
        <button class="btn primary" type="submit" :disabled="adding || !address.trim()">
          {{ t("register.add") }}
        </button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.narrow {
  max-width: 860px;
}
.steps {
  margin: 0;
  padding-left: 1.2em;
  display: grid;
  gap: 6px;
}
.add {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--fair-space-3);
}
.add .field {
  flex: 1 1 320px;
}
.add .btn {
  margin-top: 28px;
}
</style>
