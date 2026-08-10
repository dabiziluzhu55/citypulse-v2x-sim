<script setup lang="ts">
import bottomRailLeftBg from '../../../assets/design/chrome/bottom-rail-left.svg?url'
import bottomRailRightBg from '../../../assets/design/chrome/bottom-rail-right.svg?url'
import {
  CHROME_BOTTOM_RAIL_ART_WIDTH,
  CHROME_BOTTOM_RAIL_HEIGHT,
  CHROME_BOTTOM_RAIL_INNER_CAP_WIDTH,
} from '../../../constants/dashboardChromeLayout'

const capStyle = (url: string) => ({
  width: `${CHROME_BOTTOM_RAIL_INNER_CAP_WIDTH}px`,
  height: `${CHROME_BOTTOM_RAIL_HEIGHT}px`,
  backgroundImage: `url(${url})`,
  backgroundSize: `${CHROME_BOTTOM_RAIL_ART_WIDTH}px ${CHROME_BOTTOM_RAIL_HEIGHT}px`,
})
</script>

<template>
  <!-- 左横条：角帽后 → 中央底座左斜切（中段可拉伸，内侧帽固定） -->
  <div class="dashboard-bottom-rail dashboard-bottom-rail--left" aria-hidden="true">
    <div class="rail-stretch rail-stretch--left" />
    <div
      class="rail-cap rail-cap--inner rail-cap--inner-left"
      :style="capStyle(bottomRailLeftBg)"
    />
  </div>

  <!-- 右横条：中央底座右斜切 → 右角帽前 -->
  <div class="dashboard-bottom-rail dashboard-bottom-rail--right" aria-hidden="true">
    <div
      class="rail-cap rail-cap--inner rail-cap--inner-right"
      :style="capStyle(bottomRailRightBg)"
    />
    <div class="rail-stretch rail-stretch--right" />
  </div>
</template>

<style scoped>
.dashboard-bottom-rail {
  position: fixed;
  bottom: var(--dashboard-bottom-dock-offset-y, 12px);
  z-index: 5;
  pointer-events: none;
  display: flex;
  align-items: stretch;
  height: var(--dashboard-bottom-rail-height, 30px);
  min-width: 48px;
}

.dashboard-bottom-rail--left {
  left: var(--dashboard-bottom-rail-start, 106px);
  right: calc(50% - var(--dashboard-bottom-connect-half, 269.59px) + var(--dashboard-bottom-rail-overlap, 10px));
}

.dashboard-bottom-rail--right {
  left: calc(50% + var(--dashboard-bottom-connect-half, 269.59px) - var(--dashboard-bottom-rail-overlap, 10px));
  right: var(--dashboard-bottom-rail-start, 106px);
}

.rail-cap {
  flex-shrink: 0;
  background-repeat: no-repeat;
}

.rail-cap--inner-left {
  background-position: right bottom;
}

.rail-cap--inner-right {
  background-position: left bottom;
}

.rail-stretch {
  flex: 1 1 auto;
  min-width: 24px;
  position: relative;
  background: linear-gradient(180deg, #2c3b62 0%, #242f51 52%, #232e4e 100%);
  box-shadow: inset 0 -4px 8px rgba(68, 116, 185, 0.12);
}

.rail-stretch::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 33%;
  height: 2px;
  background: linear-gradient(
    90deg,
    rgba(87, 110, 148, 0.55) 0%,
    rgba(123, 160, 196, 0.85) 45%,
    rgba(116, 150, 186, 0.7) 100%
  );
  pointer-events: none;
}

.rail-stretch--right::before {
  background: linear-gradient(
    90deg,
    rgba(116, 150, 186, 0.7) 0%,
    rgba(123, 160, 196, 0.85) 55%,
    rgba(87, 110, 148, 0.55) 100%
  );
}

@media (max-width: 1320px) {
  .dashboard-bottom-rail {
    display: none;
  }
}
</style>
