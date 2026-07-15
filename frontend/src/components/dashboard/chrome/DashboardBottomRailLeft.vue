<script setup lang="ts">
import bottomRailLeftBg from '../../../assets/design/chrome/bottom-rail-left.svg?url'
import {
  CHROME_BOTTOM_RAIL_ART_WIDTH,
  CHROME_BOTTOM_RAIL_HEIGHT,
} from '../../../constants/dashboardChromeLayout'
</script>

<template>
  <!-- 左横条：左下.svg，左缘与边框底角重叠衔接，不做内部裁切避免断开感 -->
  <img
    class="dashboard-bottom-rail-left"
    :src="bottomRailLeftBg"
    :width="CHROME_BOTTOM_RAIL_ART_WIDTH"
    :height="CHROME_BOTTOM_RAIL_HEIGHT"
    alt=""
    aria-hidden="true"
  />
</template>

<style scoped>
.dashboard-bottom-rail-left {
  position: fixed;
  left: calc(var(--dashboard-bottom-rail-left-edge, 76px) + 205px);
  right: calc(50% - var(--dashboard-bottom-connect-half, 269.59px) - var(--dashboard-bottom-rail-center-gap, 500px) - var(--dashboard-bottom-rail-right-inset, 100px));
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  z-index: 5;
  pointer-events: none;
  display: block;
  width: auto;
  height: 30px;
  min-width: 120px;
  object-fit: fill;
  object-position: left bottom;
  transform: scaleX(-1);
  transform-origin: center;
}

@media (max-width: 1320px) {
  .dashboard-bottom-rail-left {
    display: none;
  }
}
</style>
