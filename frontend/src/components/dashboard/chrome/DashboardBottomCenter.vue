<script setup lang="ts">
import bottomCenterBg from '../../../assets/design/chrome/bottom-center.svg?url'
import {
  CHROME_BOTTOM_CENTER_HEIGHT,
  CHROME_BOTTOM_CENTER_WIDTH,
} from '../../../constants/dashboardChromeLayout'
</script>

<template>
  <div class="dashboard-bottom-center" aria-hidden="true">
    <img
      :src="bottomCenterBg"
      :width="CHROME_BOTTOM_CENTER_WIDTH"
      :height="CHROME_BOTTOM_CENTER_HEIGHT"
      alt=""
    />
  </div>
</template>

<style scoped>
.dashboard-bottom-center {
  position: fixed;
  left: 50%;
  bottom: var(--dashboard-bottom-center-offset-y, 12px);
  width: 629px;
  height: 58px;
  transform: translateX(-50%);
  z-index: 6;
  pointer-events: none;
}
.dashboard-bottom-center img { display: block; width: 100%; height: 100%; object-fit: fill; }
@media (max-width: 1320px) {
  .dashboard-bottom-center { transform: translateX(-50%) scale(.9); transform-origin: center bottom; }
}
</style>
