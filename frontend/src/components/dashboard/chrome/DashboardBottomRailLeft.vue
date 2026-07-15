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
  left: var(--dashboard-bottom-rail-left-edge, 76px);
  right: calc(50% - var(--dashboard-bottom-connect-half, 269.59px) - var(--dashboard-bottom-rail-center-gap, 500px) - var(--dashboard-bottom-rail-right-inset, 100px));
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  z-index: 5;
  pointer-events: none;
  display: block;
  width: auto;
  height: 30px;
  min-width: 120px;
  object-fit: fill;
  /* 右侧锚定斜切帽；左侧由容器左缘自然对齐边框，保持顶边线连续 */
  object-position: right bottom;
}

@media (max-width: 1320px) {
  .dashboard-bottom-rail-left {
    display: none;
  }
}
</style>
