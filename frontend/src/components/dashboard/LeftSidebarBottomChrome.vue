<script setup lang="ts">
import { computed } from 'vue'
import {
  LEFT_SIDEBAR_BOTTOM_CHROME,
  LEFT_SIDEBAR_CONTENT_HEIGHT,
  LEFT_SIDEBAR_CONTENT_WIDTH,
} from '../../constants/leftSidebarLayout'

const props = defineProps<{
  progressPercent?: number
}>()

const { progressRail, buttonSlots } = LEFT_SIDEBAR_BOTTOM_CHROME

const progressFillEnd = computed(() => {
  const percent = Math.min(100, Math.max(0, props.progressPercent ?? 0))
  return progressRail.x1 + (progressRail.x2 - progressRail.x1) * (percent / 100)
})
</script>

<template>
  <svg
    class="left-sidebar-bottom-chrome"
    :viewBox="`0 0 ${LEFT_SIDEBAR_CONTENT_WIDTH} ${LEFT_SIDEBAR_CONTENT_HEIGHT}`"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="ls-bottom-progress-fill" x1="295.137" y1="760" x2="17" y2="760" gradientUnits="userSpaceOnUse">
        <stop stop-color="#5CE4FF" />
        <stop offset="1" stop-color="#2491C8" stop-opacity="0.4" />
      </linearGradient>
    </defs>

    <path
      :d="`M${progressRail.x1} ${progressRail.y}H${progressRail.x2}V${progressRail.y + progressRail.height}H${progressRail.x1}V${progressRail.y}Z`"
      fill="#D0DEEE"
      fill-opacity="0.1"
    />
    <path
      v-if="progressFillEnd > progressRail.x1"
      :d="`M${progressRail.x1} ${progressRail.y}H${progressFillEnd}V${progressRail.y + progressRail.height}H${progressRail.x1}V${progressRail.y}Z`"
      fill="url(#ls-bottom-progress-fill)"
      fill-opacity="0.35"
    />

    <path
      v-for="slot in buttonSlots"
      :key="slot.id"
      :d="slot.strokePath"
      stroke="#52C2FA"
      stroke-width="2"
      fill="none"
    />
  </svg>
</template>

<style scoped>
.left-sidebar-bottom-chrome {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: visible;
}
</style>
