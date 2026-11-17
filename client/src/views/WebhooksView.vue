<script setup lang="ts">
import { reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQueryClient } from "@tanstack/vue-query";
import { WEBHOOK_EVENTS, api, type Webhook, type WebhookEvent, type WebhookInput } from "@/api";
import { ApiError } from "@/api/client";
import { keys, useWebhooks } from "@/composables/queries";
import { confirm, toast } from "@/composables/feedback";

const { t } = useI18n();
const client = useQueryClient();
const webhooks = useWebhooks();

interface Draft {
  uuid: string | null;
  payloadUrl: string;
  secret: string;
  allEvents: boolean;
  events: WebhookEvent[];
  allEntries: boolean;
  entries: string;
  enabled: boolean;
}

const editing = ref(false);
const errors = ref<Record<string, string>>({});
const draft = reactive<Draft>(empty());

function empty(): Draft {
  return {
    uuid: null,
    payloadUrl: "",
    secret: "",
    allEvents: false,
    events: ["NewEntry", "EntryUnreachable"],
    allEntries: true,
    entries: "",
    enabled: true,
  };
}

function startNew() {
  Object.assign(draft, empty(), { secret: generateSecret() });
  errors.value = {};
  editing.value = true;
}

function startEdit(hook: Webhook) {
  Object.assign(draft, {
    uuid: hook.uuid,
    payloadUrl: hook.payloadUrl,
    secret: "",
    allEvents: hook.allEvents,
    events: [...hook.events],
    allEntries: hook.allEntries,
    entries: hook.entries.join("\n"),
    enabled: hook.enabled,
  });
  errors.value = {};
  editing.value = true;
}

function generateSecret(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function save() {
  const input: WebhookInput = {
    payloadUrl: draft.payloadUrl.trim(),
    secret: draft.secret.trim() || null,
    allEvents: draft.allEvents,
    events: draft.events,
    allEntries: draft.allEntries,
    entries: draft.entries
      .split(/\s+/)
      .map((e) => e.trim())
      .filter(Boolean),
    enabled: draft.enabled,
  };
  try {
    if (draft.uuid) await api.updateWebhook(draft.uuid, input);
    else await api.createWebhook(input);
    await client.invalidateQueries({ queryKey: keys.webhooks });
    editing.value = false;
    toast(t("webhooks.saved"));
  } catch (err) {
    if (err instanceof ApiError) errors.value = err.fieldErrors;
    toast((err as Error).message, "error");
  }
}

async function remove(hook: Webhook) {
  if (
    !(await confirm(t("webhooks.deleteConfirm", { url: hook.payloadUrl }), {
      confirmLabel: t("webhooks.delete"),
      danger: true,
    }))
  )
    return;
  await api.deleteWebhook(hook.uuid);
  await client.invalidateQueries({ queryKey: keys.webhooks });
  toast(t("webhooks.deleted"));
}

async function test(hook: Webhook) {
  try {
    await api.pingWebhook(hook.uuid);
    toast(t("webhooks.testSent"));
  } catch (err) {
    toast((err as Error).message, "error");
  }
}
</script>

<template>
  <div class="page narrow">
    <header class="page-head">
      <div>
        <h1>{{ t("webhooks.title") }}</h1>
        <p class="lead">{{ t("webhooks.lead") }}</p>
      </div>
      <button v-if="!editing" class="btn primary" @click="startNew">{{ t("webhooks.add") }}</button>
    </header>

    <form v-if="editing" class="card stack" @submit.prevent="save">
      <div class="field">
        <label for="hook-url">{{ t("webhooks.url") }}</label>
        <input
          id="hook-url"
          v-model="draft.payloadUrl"
          class="input"
          type="url"
          required
          placeholder="https://"
        />
        <span v-if="errors.payloadUrl" class="error">{{ errors.payloadUrl }}</span>
      </div>
      <div class="field">
        <label for="hook-secret">{{ t("webhooks.secret") }}</label>
        <div class="row">
          <input
            id="hook-secret"
            v-model="draft.secret"
            class="input mono secret"
            autocomplete="off"
          />
          <button type="button" class="btn sm" @click="draft.secret = generateSecret()">
            {{ t("webhooks.generate") }}
          </button>
        </div>
        <span class="help">{{
          draft.uuid ? t("webhooks.secretKeep") : t("webhooks.secretHelp")
        }}</span>
        <span v-if="errors.secret" class="error">{{ errors.secret }}</span>
      </div>
      <fieldset class="field">
        <legend class="label">{{ t("webhooks.events") }}</legend>
        <label class="check"
          ><input v-model="draft.allEvents" type="checkbox" /> {{ t("webhooks.allEvents") }}</label
        >
        <div v-if="!draft.allEvents" class="events">
          <label v-for="event in WEBHOOK_EVENTS" :key="event" class="check">
            <input v-model="draft.events" type="checkbox" :value="event" />
            {{ t(`webhooks.eventNames.${event}`) }}
          </label>
        </div>
      </fieldset>
      <fieldset class="field">
        <legend class="label">{{ t("webhooks.entries") }}</legend>
        <label class="check"
          ><input v-model="draft.allEntries" type="checkbox" />
          {{ t("webhooks.allEntries") }}</label
        >
        <template v-if="!draft.allEntries">
          <textarea
            v-model="draft.entries"
            class="input"
            rows="4"
            :aria-label="t('webhooks.entries')"
          />
          <span class="help">{{ t("webhooks.entriesHelp") }}</span>
        </template>
      </fieldset>
      <label class="check"
        ><input v-model="draft.enabled" type="checkbox" /> {{ t("webhooks.enabled") }}</label
      >
      <div class="row">
        <button type="submit" class="btn primary">{{ t("webhooks.save") }}</button>
        <button type="button" class="btn" @click="editing = false">
          {{ t("webhooks.cancel") }}
        </button>
      </div>
    </form>

    <p v-if="webhooks.isPending.value" class="muted">{{ t("common.loading") }}</p>
    <p v-else-if="!webhooks.data.value?.length && !editing" class="card muted">
      {{ t("webhooks.empty") }}
    </p>
    <ul v-else class="hooks">
      <li v-for="hook in webhooks.data.value" :key="hook.uuid" class="card">
        <div class="stack info">
          <span class="mono url">{{ hook.payloadUrl }}</span>
          <div class="row">
            <span :class="['chip', hook.enabled ? 'ok' : 'warn']">
              {{ hook.enabled ? t("webhooks.enabled") : t("webhooks.disabled") }}
            </span>
            <span class="chip">{{
              hook.allEvents
                ? t("webhooks.allEvents")
                : hook.events.map((e) => t(`webhooks.eventNames.${e}`)).join(", ")
            }}</span>
            <span class="chip">{{
              hook.allEntries ? t("webhooks.allEntries") : hook.entries.length + " FDPs"
            }}</span>
          </div>
        </div>
        <div class="row">
          <button class="btn sm" @click="test(hook)">{{ t("webhooks.test") }}</button>
          <button class="btn sm" @click="startEdit(hook)">{{ t("webhooks.edit") }}</button>
          <button class="btn sm danger" @click="remove(hook)">{{ t("webhooks.delete") }}</button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.narrow {
  max-width: 860px;
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
}
.events {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
  margin-top: 6px;
}
.secret {
  flex: 1;
}
.hooks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fair-space-3);
}
.hooks li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--fair-space-3);
}
.info {
  gap: 8px;
  min-width: 0;
}
.url {
  overflow-wrap: anywhere;
  color: var(--fair-text-strong);
}
</style>
