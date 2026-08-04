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
    class="dashboard-corner-right-bottom"
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
.dashboard-corner-right-bottom {
  position: fixed;
  right: var(--dashboard-bottom-corner-offset-x, 2px);
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  z-index: 5;
  pointer-events: none;
  background-repeat: no-repeat;
  background-position: right bottom;
  overflow: hidden;
  transform: scaleX(-1);
  transform-origin: right bottom;
}

@media (max-width: 1320px) {
  .dashboard-corner-right-bottom {
    display: none;
  }
}
</style>
