<script setup lang="ts">
import bottomRailRightBg from '../../../assets/design/chrome/bottom-rail-right.svg?url'
import {
  CHROME_BOTTOM_RAIL_ART_WIDTH,
  CHROME_BOTTOM_RAIL_HEIGHT,
} from '../../../constants/dashboardChromeLayout'
</script>

<template>
  <img
    class="dashboard-bottom-rail-right"
    :src="bottomRailRightBg"
    :width="CHROME_BOTTOM_RAIL_ART_WIDTH"
    :height="CHROME_BOTTOM_RAIL_HEIGHT"
    alt=""
    aria-hidden="true"
  />
</template>

<style scoped>
.dashboard-bottom-rail-right {
  position: fixed;
  left: auto;
  right: var(--dashboard-bottom-rail-right-edge, 76px);
  width: var(--dashboard-bottom-rail-art-width, 562px);
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  height: var(--dashboard-bottom-rail-height, 30px);
  z-index: 5;
  pointer-events: none;
  display: block;
  object-fit: fill;
  /* 与左条 right bottom 对称：裁掉右侧外缘溢出 */
  object-position: left bottom;
  outline: none;
  border: none;
}

@media (max-width: 1320px) {
  .dashboard-bottom-rail-right {
    display: none;
  }
}
</style>
