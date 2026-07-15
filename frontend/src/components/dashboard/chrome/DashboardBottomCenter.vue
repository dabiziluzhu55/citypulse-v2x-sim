<script setup lang="ts">
import bottomCenterBg from '../../../assets/design/chrome/bottom-center.svg?url'
import {
  CHROME_BOTTOM_CENTER_HEIGHT,
  CHROME_BOTTOM_CENTER_WIDTH,
} from '../../../constants/dashboardChromeLayout'
</script>

<template>
  <img
    class="dashboard-bottom-center"
    :src="bottomCenterBg"
    :width="CHROME_BOTTOM_CENTER_WIDTH"
    :height="CHROME_BOTTOM_CENTER_HEIGHT"
    alt=""
    aria-hidden="true"
  />
</template>

<style scoped>
.dashboard-bottom-center {
  position: fixed;
  left: 50%;
  bottom: var(--dashboard-bottom-center-offset-y, 12px);
  transform: translateX(-50%);
  z-index: 6;
  pointer-events: none;
  display: block;
  object-fit: fill;
}

@media (max-width: 1320px) {
  .dashboard-bottom-center {
    transform: translateX(-50%) scale(0.9);
    transform-origin: center bottom;
  }
}
</style>
