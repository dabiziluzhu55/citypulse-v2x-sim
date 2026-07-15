<script setup lang="ts">
import cornerTopLeftBg from '../../../assets/design/chrome/corner-top-left.svg?url'
import cornerTopRightBg from '../../../assets/design/chrome/corner-top-right.svg?url'
</script>

<template>
  <div class="dashboard-top-rail dashboard-top-rail--left" aria-hidden="true">
    <img class="dashboard-top-rail__art" :src="cornerTopLeftBg" alt="" />
  </div>
  <div class="dashboard-top-rail dashboard-top-rail--right" aria-hidden="true">
    <img class="dashboard-top-rail__art" :src="cornerTopRightBg" alt="" />
  </div>
</template>

<style scoped>
.dashboard-top-rail {
  position: fixed;
  top: var(--dashboard-top-corner-offset-y, -8px);
  z-index: 5;
  pointer-events: none;
  height: 79px;
  overflow: hidden;
}

.dashboard-top-rail__art {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
}

/* 左上.svg：外角在 SVG 左缘，斜切朝向标题 */
.dashboard-top-rail--left {
  left: calc(var(--dashboard-top-corner-inset, 16px) - var(--dashboard-frame-edge-outset, 14px));
  right: calc(
    50% + var(--dashboard-top-title-half, 387.5px) - var(--dashboard-top-rail-center-bias, 80px)
  );
}

.dashboard-top-rail--left .dashboard-top-rail__art {
  object-position: left top;
}

/* 左上 (1).svg：外角在 SVG 右缘，斜切朝向标题 */
.dashboard-top-rail--right {
  right: calc(var(--dashboard-top-corner-inset, 16px) - var(--dashboard-frame-edge-outset, 14px));
  left: calc(
    50% + var(--dashboard-top-title-half, 387.5px) - var(--dashboard-top-rail-center-bias, 80px)
  );
}

.dashboard-top-rail--right .dashboard-top-rail__art {
  object-position: right top;
}

@media (max-width: 1320px) {
  .dashboard-top-rail {
    display: none;
  }
}
</style>
