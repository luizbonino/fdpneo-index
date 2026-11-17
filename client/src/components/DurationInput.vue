<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { type DurationUnit, toAmount, toIso } from "@/utils/format";

const model = defineModel<string>({ required: true });
const props = defineProps<{ id: string; units?: DurationUnit[] }>();
const { t } = useI18n();

const units = computed<DurationUnit[]>(() => props.units ?? ["minutes", "hours", "days"]);
const parts = computed(() => toAmount(model.value));

function setAmount(event: Event) {
  const amount = Number((event.target as HTMLInputElement).value);
  if (Number.isFinite(amount) && amount >= 0) model.value = toIso(amount, parts.value.unit);
}

function setUnit(event: Event) {
  model.value = toIso(
    parts.value.amount,
    (event.target as HTMLSelectElement).value as DurationUnit,
  );
}
</script>

<template>
  <div class="duration">
    <input
      :id="id"
      class="input amount"
      type="number"
      min="0"
      step="1"
      :value="parts.amount"
      @input="setAmount"
    />
    <select
      class="input unit"
      :value="parts.unit"
      :aria-label="t('settings.unit')"
      @change="setUnit"
    >
      <option v-for="unit in units" :key="unit" :value="unit">
        {{ t(`settings.units.${unit}`) }}
      </option>
      <option v-if="!units.includes(parts.unit)" :value="parts.unit">
        {{ t(`settings.units.${parts.unit}`) }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.duration {
  display: flex;
  gap: var(--fair-space-2);
  max-width: 320px;
}
.amount {
  width: 110px;
}
.unit {
  width: auto;
  flex: 1;
}
</style>
