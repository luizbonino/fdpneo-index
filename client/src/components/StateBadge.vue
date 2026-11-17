<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { EntryState } from "@/api";
import { STATE_TONE } from "@/utils/format";

const props = defineProps<{ state: string }>();
const { t } = useI18n();

const known = computed(() => (props.state in STATE_TONE ? (props.state as EntryState) : "UNKNOWN"));
const tone = computed(() => STATE_TONE[known.value]);
</script>

<template>
  <span :class="['chip', tone]" :title="t(`state.help.${known}`)">
    <span class="dot" aria-hidden="true" />
    {{ t(`state.${known}`) }}
  </span>
</template>
