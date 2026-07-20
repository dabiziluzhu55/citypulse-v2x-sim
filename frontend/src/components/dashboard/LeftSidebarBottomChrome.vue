<script setup lang="ts">
import {
  LEFT_SIDEBAR_BOTTOM_CHROME,
  LEFT_SIDEBAR_CONTENT_HEIGHT,
  LEFT_SIDEBAR_CONTENT_WIDTH,
} from '../../constants/leftSidebarLayout'

const { buttonSlots } = LEFT_SIDEBAR_BOTTOM_CHROME

</script>

<template>
  <svg
    class="left-sidebar-bottom-chrome"
    :viewBox="`0 0 ${LEFT_SIDEBAR_CONTENT_WIDTH} ${LEFT_SIDEBAR_CONTENT_HEIGHT}`"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
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
