<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { api, type Permit } from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import { useInvalidateEntries } from "@/composables/queries";
import { toast } from "@/composables/feedback";
import CopyField from "@/components/CopyField.vue";
import StateBadge from "@/components/StateBadge.vue";
import CheckPanel from "@/components/health/CheckPanel.vue";
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

// Checking first (TASKS 15.11): open to anyone or to administrators, as the index says.
const checks = useQuery({
  queryKey: computed(() => ["check-policy", session.isAdmin]),
  queryFn: () => api.checkPolicy(),
});
async function registerChecked(url: string) {
  address.value = url;
  await nextTick();
  document.getElementById("add")?.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("fdp-address")?.focus({ preventScroll: true });
}

const address = ref("");
const error = ref("");
const adding = ref(false);

/** What the immediate check found (TASKS 4.9). */
interface Outcome {
  uuid: string;
  clientUrl: string;
  state: string;
  permit: Permit;
  newEntry: boolean;
  retrieved: boolean;
  error: string | null;
  title: string | null;
}
const outcome = ref<Outcome | null>(null);

async function add() {
  error.value = "";
  outcome.value = null;
  adding.value = true;
  const url = address.value.trim();
  try {
    if (session.isAdmin) {
      const detail = await api.createEntry(url);
      outcome.value = {
        uuid: detail.uuid,
        clientUrl: detail.clientUrl,
        state: detail.state,
        permit: detail.permit,
        newEntry: true,
        retrieved: true,
        error: null,
        title: detail.currentMetadata?.metadata.title ?? null,
      };
    } else {
      outcome.value = await api.register(url);
    }
    address.value = "";
    toast(t("register.checked", { url }));
    setTimeout(() => void invalidate(), 2500);
  } catch (err) {
    error.value =
      err instanceof ApiError ? (Object.values(err.fieldErrors)[0] ?? err.message) : String(err);
  } finally {
    adding.value = false;
  }
}

const verdict = computed(() => {
  const found = outcome.value;
  if (!found) return "";
  if (found.state === "UNREACHABLE") return "unreachable";
  if (found.state === "INVALID") return "invalid";
  if (found.permit === "PENDING") return "pending";
  if (found.permit === "REJECTED") return "rejected";
  return "listed";
});
</script>

<template>
  <div class="page narrow">
    <header class="page-head">
      <div>
        <h1>{{ t("register.title") }}</h1>
        <p class="lead">{{ t("register.lead") }}</p>
        <p>
          <router-link :to="{ name: 'expectations' }">{{ t("expectations.link") }}</router-link>
        </p>
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

    <section v-if="checks.data.value?.allowed" id="check" class="card stack">
      <h2>{{ t("check.title") }}</h2>
      <p class="muted">
        {{ t("check.help", { pages: checks.data.value.pages, n: checks.data.value.perPage }) }}
      </p>
      <CheckPanel
        :pages="checks.data.value.pages"
        :per-page="checks.data.value.perPage"
        @register="registerChecked"
      />
    </section>

    <section id="add" class="card stack">
      <h2>{{ t("register.nowTitle") }}</h2>
      <p class="muted">
        {{ session.isAdmin ? t("register.adminHelp") : t("register.nowHelp") }}
      </p>
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
          {{ adding ? t("register.checking") : t("register.add") }}
        </button>
      </form>
      <div v-if="outcome" class="outcome" role="status">
        <div class="row">
          <StateBadge :state="outcome.state" />
          <strong>{{ outcome.title || outcome.clientUrl }}</strong>
        </div>
        <p>{{ t(`register.verdict.${verdict}`) }}</p>
        <p v-if="outcome.error" class="muted small">{{ outcome.error }}</p>
        <p v-if="!outcome.retrieved" class="muted small">{{ t("register.recentlyChecked") }}</p>
        <router-link
          v-if="outcome.permit === 'ACCEPTED' || session.isAdmin"
          class="btn sm"
          :to="{ name: 'entry', params: { uuid: outcome.uuid } }"
        >
          {{ t("register.open") }}
        </router-link>
      </div>
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
.outcome {
  display: grid;
  gap: var(--fair-space-2);
  padding: var(--fair-space-3) var(--fair-space-4);
  border-radius: var(--fair-radius-md, 8px);
  background: var(--fair-surface-2, var(--tool-accent-tint));
}
.outcome p {
  margin: 0;
}
</style>
