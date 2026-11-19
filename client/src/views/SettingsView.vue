<script setup lang="ts">
/**
 * Index settings. Durations are edited as an amount and a unit; the deny list
 * is validated as you type and can be tried against an address. The browser's
 * regular expressions approximate the server's (Python) ones; the server has
 * the last word when saving.
 */
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useQueryClient } from "@tanstack/vue-query";
import { PERMITS, api, type Settings, type SettingsUpdate } from "@/api";
import { ApiError } from "@/api/client";
import { keys, useSettings } from "@/composables/queries";
import { confirm, toast } from "@/composables/feedback";
import DurationInput from "@/components/DurationInput.vue";
import FacetSettings from "@/components/search/FacetSettings.vue";
import ProfileEditor from "@/components/search/ProfileEditor.vue";

const { t } = useI18n();
const client = useQueryClient();
const settings = useSettings();

// The response shape minus isDefault: every group present, unlike the request type.
type SettingsForm = Omit<Settings, "isDefault">;

const form = reactive<SettingsForm>({
  autoPermit: true,
  ping: { validDuration: "PT168H", rateLimitDuration: "PT6H", rateLimitHits: 10, denyList: [] },
  retrieval: { rateLimitWait: "PT10M", timeout: "PT1M" },
  healthCheck: {
    enabled: true,
    interval: "PT24H",
    failureThreshold: 3,
    permits: ["ACCEPTED"],
    reharvestOnChange: true,
  },
  verifiedPermit: false,
  expiry: { enabled: false, after: "PT2160H", hide: false },
});
const errors = ref<Record<string, string>>({});
const saving = ref(false);

function load(value: Settings) {
  form.autoPermit = value.autoPermit;
  form.ping = { ...value.ping, denyList: [...value.ping.denyList] };
  form.retrieval = { ...value.retrieval };
  form.healthCheck = { ...value.healthCheck, permits: [...value.healthCheck.permits] };
  form.verifiedPermit = value.verifiedPermit;
  form.expiry = { ...value.expiry };
}

/** Automatic, after a successful check (verified), or manual: two settings in the API. */
type Approval = "automatic" | "verified" | "manual";
const approval = computed<Approval>({
  get: () => (form.autoPermit ? "automatic" : form.verifiedPermit ? "verified" : "manual"),
  set: (value) => {
    form.autoPermit = value === "automatic";
    form.verifiedPermit = value === "verified";
  },
});
const APPROVALS: Approval[] = ["automatic", "verified", "manual"];
watch(
  () => settings.data.value,
  (value) => value && load(value),
  { immediate: true },
);

function patternError(pattern: string): string | null {
  try {
    new RegExp(pattern);
    return null;
  } catch {
    return t("settings.invalidPattern");
  }
}

const invalid = computed(() => form.ping.denyList.some((p) => p.trim() && patternError(p)));

const testUrl = ref("");
const testResult = computed(() => {
  const url = testUrl.value.trim();
  if (!url) return null;
  const match = form.ping.denyList.find((pattern) => {
    if (!pattern.trim() || patternError(pattern)) return false;
    return new RegExp(`^(?:${pattern})$`).test(url);
  });
  return match ? { denied: true, pattern: match } : { denied: false, pattern: "" };
});

function addPattern() {
  form.ping.denyList.push("");
}

function removePattern(index: number) {
  form.ping.denyList.splice(index, 1);
}

async function save() {
  saving.value = true;
  errors.value = {};
  try {
    const body: SettingsUpdate = {
      ...form,
      ping: { ...form.ping, denyList: form.ping.denyList.filter((p) => p.trim()) },
    };
    const saved = await api.saveSettings(body);
    client.setQueryData(keys.settings, saved);
    load(saved);
    toast(t("settings.saved"));
  } catch (err) {
    if (err instanceof ApiError) errors.value = err.fieldErrors;
    toast((err as Error).message, "error");
  } finally {
    saving.value = false;
  }
}

async function reset() {
  if (
    !(await confirm(t("settings.resetConfirm"), {
      confirmLabel: t("settings.reset"),
      danger: true,
    }))
  )
    return;
  const saved = await api.resetSettings();
  client.setQueryData(keys.settings, saved);
  load(saved);
  toast(t("settings.resetDone"));
}

function fieldError(path: string): string | undefined {
  return Object.entries(errors.value).find(([key]) => key.endsWith(path))?.[1];
}
</script>

<template>
  <div class="page narrow">
    <header class="page-head">
      <div>
        <h1>{{ t("settings.title") }}</h1>
        <p class="lead">{{ t("settings.lead") }}</p>
      </div>
      <span
        v-if="settings.data.value"
        :class="['chip', settings.data.value.isDefault ? 'info' : 'warn']"
      >
        {{ settings.data.value.isDefault ? t("settings.default") : t("settings.customised") }}
      </span>
    </header>

    <p v-if="settings.isPending.value" class="muted">{{ t("common.loading") }}</p>
    <form v-else class="stack form" @submit.prevent="save">
      <section class="card stack">
        <h2>{{ t("settings.registration") }}</h2>
        <fieldset class="stack plain">
          <legend class="label">{{ t("settings.approval") }}</legend>
          <label v-for="option in APPROVALS" :key="option" class="check approval">
            <input v-model="approval" type="radio" name="approval" :value="option" />
            <span>
              {{ t(`settings.approvals.${option}`) }}
              <span class="help muted small">{{ t(`settings.approvalsHelp.${option}`) }}</span>
            </span>
          </label>
        </fieldset>
      </section>

      <section class="card stack">
        <h2>{{ t("settings.pings") }}</h2>
        <div class="field">
          <label for="valid-duration">{{ t("settings.validDuration") }}</label>
          <DurationInput id="valid-duration" v-model="form.ping.validDuration" />
          <span class="help">{{ t("settings.validDurationHelp") }}</span>
          <span v-if="fieldError('validDuration')" class="error">{{
            fieldError("validDuration")
          }}</span>
        </div>
        <div class="field">
          <span class="label">{{ t("settings.rateLimit") }}</span>
          <div class="row">
            <span class="muted">{{ t("settings.rateLimitHits") }}</span>
            <input
              v-model.number="form.ping.rateLimitHits"
              class="input hits"
              type="number"
              min="0"
              :aria-label="t('settings.rateLimit')"
            />
            <span class="muted">{{ t("settings.rateLimitPer") }}</span>
            <DurationInput id="rate-limit-duration" v-model="form.ping.rateLimitDuration" />
          </div>
          <span class="help">{{ t("settings.rateLimitHelp") }}</span>
        </div>
      </section>

      <section class="card stack">
        <h2>{{ t("settings.checks") }}</h2>
        <div class="field">
          <label for="rate-limit-wait">{{ t("settings.rateLimitWait") }}</label>
          <DurationInput
            id="rate-limit-wait"
            v-model="form.retrieval.rateLimitWait"
            :units="['seconds', 'minutes', 'hours']"
          />
          <span class="help">{{ t("settings.rateLimitWaitHelp") }}</span>
        </div>
        <div class="field">
          <label for="timeout">{{ t("settings.timeout") }}</label>
          <DurationInput
            id="timeout"
            v-model="form.retrieval.timeout"
            :units="['seconds', 'minutes']"
          />
          <span v-if="fieldError('timeout')" class="error">{{ fieldError("timeout") }}</span>
        </div>
      </section>

      <section class="card stack">
        <h2>{{ t("settings.health") }}</h2>
        <p class="muted small">{{ t("settings.healthLead") }}</p>
        <label class="check">
          <input v-model="form.healthCheck.enabled" type="checkbox" />
          {{ t("settings.healthEnabled") }}
        </label>
        <fieldset class="stack plain" :disabled="!form.healthCheck.enabled">
          <div class="field">
            <label for="health-interval">{{ t("settings.healthInterval") }}</label>
            <DurationInput
              id="health-interval"
              v-model="form.healthCheck.interval"
              :units="['minutes', 'hours', 'days']"
            />
          </div>
          <div class="field">
            <label for="health-threshold">{{ t("settings.healthThreshold") }}</label>
            <div class="row">
              <input
                id="health-threshold"
                v-model.number="form.healthCheck.failureThreshold"
                class="input hits"
                type="number"
                min="1"
                max="100"
              />
              <span class="muted">{{ t("settings.healthThresholdSuffix") }}</span>
            </div>
            <span class="help">{{ t("settings.healthThresholdHelp") }}</span>
          </div>
          <div class="field">
            <span class="label">{{ t("settings.healthPermits") }}</span>
            <div class="row">
              <label v-for="permit in PERMITS" :key="permit" class="check">
                <input v-model="form.healthCheck.permits" type="checkbox" :value="permit" />
                {{ t(`permit.${permit}`) }}
              </label>
            </div>
          </div>
          <label class="check">
            <input v-model="form.healthCheck.reharvestOnChange" type="checkbox" />
            {{ t("settings.healthReharvest") }}
          </label>
        </fieldset>
      </section>

      <section class="card stack">
        <h2>{{ t("settings.expiry") }}</h2>
        <p class="muted small">{{ t("settings.expiryLead") }}</p>
        <label class="check">
          <input v-model="form.expiry.enabled" type="checkbox" />
          {{ t("settings.expiryEnabled") }}
        </label>
        <fieldset class="stack plain" :disabled="!form.expiry.enabled">
          <div class="field">
            <label for="expiry-after">{{ t("settings.expiryAfter") }}</label>
            <DurationInput id="expiry-after" v-model="form.expiry.after" :units="['days']" />
            <span v-if="fieldError('after')" class="error">{{ fieldError("after") }}</span>
          </div>
          <label class="check">
            <input v-model="form.expiry.hide" type="checkbox" />
            {{ t("settings.expiryHide") }}
          </label>
        </fieldset>
      </section>

      <section class="card stack">
        <h2>{{ t("settings.denyList") }}</h2>
        <p class="muted small">{{ t("settings.denyListHelp") }}</p>
        <div v-for="(_, index) in form.ping.denyList" :key="index" class="pattern">
          <input
            v-model="form.ping.denyList[index]"
            class="input mono"
            :aria-label="`${t('settings.denyList')} ${index + 1}`"
            :aria-invalid="!!patternError(form.ping.denyList[index] ?? '')"
            :placeholder="t('settings.patternPlaceholder')"
          />
          <button type="button" class="btn ghost sm" @click="removePattern(index)">
            {{ t("settings.removePattern") }}
          </button>
          <span v-if="patternError(form.ping.denyList[index] ?? '')" class="error small">
            {{ patternError(form.ping.denyList[index] ?? "") }}
          </span>
        </div>
        <span v-if="fieldError('denyList')" class="error small">{{ fieldError("denyList") }}</span>
        <div>
          <button type="button" class="btn sm" @click="addPattern">
            {{ t("settings.addPattern") }}
          </button>
        </div>
        <div class="field tester">
          <label for="deny-test">{{ t("settings.testUrl") }}</label>
          <input
            id="deny-test"
            v-model="testUrl"
            class="input"
            :placeholder="t('settings.testPlaceholder')"
          />
          <span v-if="testResult" :class="['chip', testResult.denied ? 'bad' : 'ok']" role="status">
            {{
              testResult.denied
                ? t("settings.testDenied", { pattern: testResult.pattern })
                : t("settings.testAllowed")
            }}
          </span>
        </div>
      </section>

      <div class="row actions">
        <button type="button" class="btn danger" @click="reset">{{ t("settings.reset") }}</button>
        <span class="spacer" />
        <button type="submit" class="btn primary" :disabled="saving || invalid">
          {{ t("settings.save") }}
        </button>
      </div>
    </form>

    <FacetSettings class="facet-settings" />
    <ProfileEditor class="facet-settings" />
  </div>
</template>

<style scoped>
.narrow {
  max-width: 860px;
}
.form {
  gap: var(--fair-space-4);
}
.hits {
  width: 90px;
}
.plain {
  border: 0;
  padding: 0;
  margin: 0;
}
.approval {
  align-items: flex-start;
}
.approval .help {
  display: block;
}
.pattern {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px var(--fair-space-2);
  align-items: center;
}
.pattern .error {
  grid-column: 1 / -1;
}
.tester {
  margin-top: var(--fair-space-3);
  padding-top: var(--fair-space-4);
  border-top: 1px solid var(--fair-separator);
  justify-items: start;
}
.actions {
  position: sticky;
  bottom: 0;
  padding: var(--fair-space-3) 0;
  background: var(--fair-bg);
}
.spacer {
  flex: 1;
}
.facet-settings {
  margin-top: var(--fair-space-5);
}
</style>
