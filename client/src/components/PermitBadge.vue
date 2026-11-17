<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { Permit } from "@/api";

const props = defineProps<{ permit: Permit }>();
const { t } = useI18n();
const tone = computed(() =>
  props.permit === "ACCEPTED" ? "info" : props.permit === "PENDING" ? "warn" : "bad",
);
</script>

<template>
  <span :class="['chip', tone]">{{ t(`permit.${permit}`) }}</span>
</template>
