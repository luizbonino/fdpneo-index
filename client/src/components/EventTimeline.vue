<script setup lang="ts">
/**
 * An entry's events, newest first. Administrators can open an event to see
 * the HTTP exchange it recorded (request, response status, error).
 */
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { request } from "@/api/client";
import type { EventDetail, EventSummary } from "@/api";
import { absoluteTime, relativeTime } from "@/utils/format";

defineProps<{ events: EventSummary[]; detailed: boolean }>();
const { t } = useI18n();

const open = ref<string | null>(null);
const details = ref<Record<string, EventDetail>>({});

async function toggle(uuid: string) {
  if (open.value === uuid) {
    open.value = null;
    return;
  }
  open.value = uuid;
  details.value[uuid] ??= await request<EventDetail>(`/index/events/${uuid}`);
}

interface ExchangeView {
  state?: string;
  error?: string | null;
  request?: { method?: string | null; url?: string | null };
  response?: { code?: number | null; body?: string | null };
}

function exchangeOf(detail: EventDetail | undefined): ExchangeView | null {
  if (!detail) return null;
  const payload = detail.payload as Record<string, { exchange?: ExchangeView } | undefined>;
  for (const part of Object.values(payload)) {
    if (part?.exchange) return part.exchange;
  }
  return null;
}

function tone(event: EventSummary): string {
  if (!event.finished) return "pending";
  const outcome = event.outcome ?? "";
  if (/error|unreach|rate limit|cannot|not found|HTTP [45]/i.test(outcome)) return "warn";
  return "ok";
}
</script>

<template>
  <p v-if="!events.length" class="muted">{{ t("events.none") }}</p>
  <ol v-else class="timeline">
    <li v-for="event in events" :key="event.uuid" :class="tone(event)">
      <span class="marker" aria-hidden="true" />
      <div class="body">
        <div class="head">
          <strong>{{ t(`events.${event.type}`) }}</strong>
          <span v-if="event.outcome" class="outcome">{{ event.outcome }}</span>
          <span v-if="!event.finished" class="chip warn">{{ t("events.pending") }}</span>
          <time class="muted small" :datetime="event.created" :title="absoluteTime(event.created)">
            {{ relativeTime(event.created) }}
          </time>
          <button
            v-if="detailed"
            class="btn ghost sm"
            :aria-expanded="open === event.uuid"
            @click="toggle(event.uuid)"
          >
            {{ t("events.details") }}
          </button>
        </div>
        <div v-if="open === event.uuid" class="detail">
          <template v-if="exchangeOf(details[event.uuid])">
            <dl class="facts small">
              <dt>{{ t("events.request") }}</dt>
              <dd class="mono">
                {{ exchangeOf(details[event.uuid])?.request?.method }}
                {{ exchangeOf(details[event.uuid])?.request?.url }}
              </dd>
              <dt>{{ t("events.response") }}</dt>
              <dd class="mono">
                {{
                  exchangeOf(details[event.uuid])?.response?.code ??
                  exchangeOf(details[event.uuid])?.error ??
                  exchangeOf(details[event.uuid])?.state
                }}
              </dd>
            </dl>
            <pre v-if="exchangeOf(details[event.uuid])?.response?.body" class="code">{{
              exchangeOf(details[event.uuid])?.response?.body
            }}</pre>
          </template>
          <pre v-else-if="details[event.uuid]" class="code">{{
            JSON.stringify(details[event.uuid]?.payload, null, 2)
          }}</pre>
        </div>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}
.timeline li {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: var(--fair-space-3);
  padding-bottom: var(--fair-space-4);
  position: relative;
}
.timeline li:not(:last-child)::before {
  content: "";
  position: absolute;
  left: 5px;
  top: 16px;
  bottom: 0;
  width: 2px;
  background: var(--fair-separator);
}
.marker {
  width: 12px;
  height: 12px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--fair-success);
  box-shadow: 0 0 0 3px var(--fair-surface);
}
.warn .marker {
  background: var(--fair-warning);
}
.pending .marker {
  background: var(--fair-text-light);
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--fair-space-2) var(--fair-space-3);
}
.outcome {
  color: var(--fair-text);
}
.detail {
  margin-top: var(--fair-space-3);
  display: grid;
  gap: var(--fair-space-3);
}
</style>
