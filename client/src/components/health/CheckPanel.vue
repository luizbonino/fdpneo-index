<script setup lang="ts">
/**
 * Check an FDP before registering it (TASKS 15.11): the index reads its root
 * metadata and a sample of its pages, as it would after registering, and
 * shows the metadata health of the sample. Nothing is stored. With `url`
 * (an administrator reviewing a pending FDP) there is no address field.
 */
import { computed, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, type Check } from "@/api";
import { ApiError } from "@/api/client";
import { session } from "@/auth/session";
import HealthReport from "./HealthReport.vue";

const props = defineProps<{ url?: string; pages?: number; perPage?: number }>();
const emit = defineEmits<{ register: [url: string] }>();
const { t } = useI18n();

const address = ref("");
const error = ref("");
const check = ref<Check | null>(null);
let timer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(timer));

const running = computed(() => check.value?.status === "running");
const TONE = { fine: "ok", findings: "warn", unreadable: "bad" } as const;

async function poll(id: string) {
  try {
    check.value = await api.check(id);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : String(err);
    return;
  }
  if (check.value.status === "running") timer = setTimeout(() => void poll(id), 2000);
}

async function start() {
  clearTimeout(timer);
  error.value = "";
  check.value = null;
  const url = (props.url ?? address.value).trim();
  try {
    check.value = await api.startCheck(url);
  } catch (err) {
    error.value =
      err instanceof ApiError ? (Object.values(err.fieldErrors)[0] ?? err.message) : String(err);
    return;
  }
  if (check.value.status === "running") timer = setTimeout(() => void poll(check.value!.id), 1000);
}

const verdict = computed(() => {
  const root = check.value?.root;
  if (!root) return "";
  if (root.state === "VALID") return "valid";
  return root.state === "UNREACHABLE" ? "unreachable" : "invalid";
});
</script>

<template>
  <div class="stack">
    <form v-if="!url" class="add" @submit.prevent="start">
      <div class="field">
        <label for="check-address">{{ t("register.addressLabel") }}</label>
        <input
          id="check-address"
          v-model="address"
          class="input"
          type="url"
          required
          placeholder="https://fdp.example.org"
          :aria-invalid="!!error"
          aria-describedby="check-address-error"
        />
        <span id="check-address-error" class="error" role="alert">{{ error }}</span>
      </div>
      <button class="btn" type="submit" :disabled="running || !address.trim()">
        {{ running ? t("check.running") : t("check.start") }}
      </button>
    </form>
    <div v-else class="row">
      <button class="btn" type="button" :disabled="running" @click="start">
        {{ running ? t("check.running") : check ? t("check.again") : t("check.start") }}
      </button>
      <span v-if="error" class="error" role="alert">{{ error }}</span>
    </div>

    <p v-if="running" class="muted small" role="status">
      {{ t("check.wait", { pages: pages ?? 200, n: perPage ?? 5 }) }}
    </p>

    <div v-if="check && !running" class="result stack" role="status">
      <div class="row head">
        <strong>{{ check.root?.title || check.clientUrl }}</strong>
        <span v-if="check.report" :class="['chip', TONE[check.report.status]]">
          {{ t(`health.status.${check.report.status}`) }}
        </span>
      </div>
      <p>{{ t(`check.verdict.${verdict}`) }}</p>
      <p v-if="check.root?.error" class="muted small">{{ check.root.error }}</p>
      <p v-if="check.registered && !url" class="small">
        {{ t("check.registered") }}
        <router-link
          v-if="check.registered.permit === 'ACCEPTED' || session.isAdmin"
          :to="{ name: 'entry', params: { uuid: check.registered.uuid } }"
        >
          {{ t("register.open") }}
        </router-link>
      </p>
      <HealthReport
        v-if="check.report"
        :report="check.report"
        :sample="{ sampledOut: check.sampledOut ?? 0 }"
      />
      <p v-if="check.report" class="muted small">{{ t("check.afterwards") }}</p>
      <div v-if="!url && verdict === 'valid' && !check.registered" class="row">
        <button class="btn primary sm" type="button" @click="emit('register', check.clientUrl)">
          {{ t("check.register") }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
.head {
  gap: var(--fair-space-3);
}
.result {
  padding-top: var(--fair-space-3);
  border-top: 1px solid var(--fair-separator);
}
.result > p {
  margin: 0;
}
</style>
