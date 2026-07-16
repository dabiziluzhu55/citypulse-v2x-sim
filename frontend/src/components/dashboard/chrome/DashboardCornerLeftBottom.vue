<script setup lang="ts">
import cornerLeftBottomBg from '../../../assets/design/chrome/corner-left-bottom.svg?url'
import {
  CHROME_BOTTOM_CORNER_CAP_WIDTH,
  CHROME_BOTTOM_RAIL_ART_WIDTH,
  CHROME_CORNER_LEFT_BOTTOM_HEIGHT,
} from '../../../constants/dashboardChromeLayout'
</script>

<template>
  <div
    class="dashboard-corner-left-bottom"
    :style="{
      width: `${CHROME_BOTTOM_CORNER_CAP_WIDTH}px`,
      height: `${CHROME_CORNER_LEFT_BOTTOM_HEIGHT}px`,
      backgroundImage: `url(${cornerLeftBottomBg})`,
      backgroundSize: `${CHROME_BOTTOM_RAIL_ART_WIDTH}px ${CHROME_CORNER_LEFT_BOTTOM_HEIGHT}px`,
    }"
    aria-hidden="true"
  />
</template>

<style scoped>
.dashboard-corner-left-bottom {
  position: fixed;
  left: var(--dashboard-bottom-corner-offset-x, 2px);
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  z-index: 5;
  pointer-events: none;
  background-repeat: no-repeat;
  background-position: left bottom;
  overflow: hidden;
}

@media (max-width: 1320px) {
  .dashboard-corner-left-bottom {
    display: none;
  }
}
</style>
