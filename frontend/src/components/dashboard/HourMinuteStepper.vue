<script setup lang="ts">
import { computed } from 'vue'
import {
  clampClockTime,
  clockTimeToMinutes,
  stepClockHour,
  stepClockMinute,
} from '../../constants/scenarioOptions'

const props = withDefaults(defineProps<{
  modelValue: string
  minimum: string
  maximum: string
  disabled?: boolean
  label: string
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const normalizedValue = computed(() => clampClockTime(
  props.modelValue,
  props.minimum,
  props.maximum,
))
const hour = computed(() => normalizedValue.value.slice(0, 2))
const minute = computed(() => normalizedValue.value.slice(3, 5))
const currentMinutes = computed(() => clockTimeToMinutes(normalizedValue.value))
const minimumMinutes = computed(() => clockTimeToMinutes(props.minimum))
const maximumMinutes = computed(() => clockTimeToMinutes(props.maximum))
const minimumHour = computed(() => Math.floor(minimumMinutes.value / 60))
const maximumHour = computed(() => Math.floor(maximumMinutes.value / 60))
const currentHour = computed(() => Math.floor(currentMinutes.value / 60))

function update(next: string): void {
  if (props.disabled || next === normalizedValue.value) return
  emit('update:modelValue', next)
}

function stepHour(direction: -1 | 1): void {
  update(stepClockHour(normalizedValue.value, direction, props.minimum, props.maximum))
}

function stepMinute(direction: -1 | 1): void {
  update(stepClockMinute(normalizedValue.value, direction, props.minimum, props.maximum))
}
</script>

<template>
  <div
    class="hour-minute-stepper"
    :class="{ 'is-disabled': disabled }"
    role="group"
    :aria-label="label"
  >
    <div class="hour-minute-stepper__part">
      <button
        type="button"
        class="hour-minute-stepper__control is-increase"
        :aria-label="`${label}小时增加`"
        :disabled="disabled || currentHour >= maximumHour"
        @click="stepHour(1)"
      ><i aria-hidden="true" /></button>
      <output :aria-label="`${label}小时`">{{ hour }}</output>
      <button
        type="button"
        class="hour-minute-stepper__control is-decrease"
        :aria-label="`${label}小时减少`"
        :disabled="disabled || currentHour <= minimumHour"
        @click="stepHour(-1)"
      ><i aria-hidden="true" /></button>
    </div>
    <b aria-hidden="true">:</b>
    <div class="hour-minute-stepper__part">
      <button
        type="button"
        class="hour-minute-stepper__control is-increase"
        :aria-label="`${label}分钟增加`"
        :disabled="disabled || currentMinutes >= maximumMinutes"
        @click="stepMinute(1)"
      ><i aria-hidden="true" /></button>
      <output :aria-label="`${label}分钟`">{{ minute }}</output>
      <button
        type="button"
        class="hour-minute-stepper__control is-decrease"
        :aria-label="`${label}分钟减少`"
        :disabled="disabled || currentMinutes <= minimumMinutes"
        @click="stepMinute(-1)"
      ><i aria-hidden="true" /></button>
    </div>
  </div>
</template>

<style scoped>
.hour-minute-stepper {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 8px minmax(0, 1fr);
  align-items: center;
  min-width: 0;
  height: 48px;
  border: 1px solid rgba(82, 194, 250, .52);
  border-radius: 4px;
  background: #071f38;
  box-shadow: inset 0 0 12px rgba(30, 136, 205, .08);
  color: #fff;
}
.hour-minute-stepper:focus-within {
  border-color: #52c2fa;
  box-shadow: 0 0 0 1px rgba(82, 194, 250, .22), inset 0 0 12px rgba(30, 136, 205, .1);
}
.hour-minute-stepper__part {
  display: grid;
  grid-template-columns: 18px minmax(22px, 1fr) 18px;
  align-items: center;
  min-width: 0;
  height: 100%;
}
.hour-minute-stepper__part output {
  display: block;
  min-width: 0;
  color: #fff;
  font: 700 14px/1 'DIN Alternate', 'Microsoft YaHei', sans-serif;
  letter-spacing: 0;
  text-align: center;
}
.hour-minute-stepper > b {
  color: rgba(158, 220, 242, .78);
  font-size: 14px;
  text-align: center;
}
.hour-minute-stepper__control {
  position: relative;
  width: 18px;
  height: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: #9edcf2;
  cursor: pointer;
}
.hour-minute-stepper__control:hover:not(:disabled),
.hour-minute-stepper__control:focus-visible {
  background: rgba(82, 194, 250, .14);
  color: #fff;
  outline: none;
}
.hour-minute-stepper__control i {
  position: absolute;
  left: 6px;
  top: 50%;
  width: 6px;
  height: 6px;
  border-top: 1px solid currentColor;
  border-right: 1px solid currentColor;
}
.hour-minute-stepper__control.is-increase i { transform: translateY(-1px) rotate(-45deg); }
.hour-minute-stepper__control.is-decrease i { transform: translateY(-5px) rotate(135deg); }
.hour-minute-stepper__control:disabled { color: rgba(126, 155, 176, .26); cursor: not-allowed; }
.hour-minute-stepper.is-disabled { opacity: .52; }
</style>
