<script setup lang="ts">
import headerTitleBg from '../../../assets/design/chrome/header-title.svg?url'
import collegeEmblem from '../../../assets/design/branding/college-emblem.svg?url'
import universityEmblem from '../../../assets/design/branding/university-emblem.svg?url'
import {
  CHROME_BRAND_INSET_LEFT,
  CHROME_BRAND_INSET_TOP,
  CHROME_CLOCK_INSET_RIGHT,
  CHROME_CLOCK_INSET_TOP,
  CHROME_HEADER_HEIGHT,
  CHROME_HEADER_TITLE_WIDTH,
  DASHBOARD_TITLE,
} from '../../../constants/dashboardChromeLayout'
import { useDashboardClock } from '../../../composables/useDashboardClock'

const { nowLabel } = useDashboardClock()
</script>

<template>
  <header class="dashboard-header">
    <div
      class="dashboard-header__brand"
      :style="{
        left: `${CHROME_BRAND_INSET_LEFT}px`,
        top: `${CHROME_BRAND_INSET_TOP}px`,
      }"
    >
      <img
        class="dashboard-header__emblem dashboard-header__emblem--university"
        :src="universityEmblem"
        alt="中国地质大学"
      />
      <img
        class="dashboard-header__emblem dashboard-header__emblem--college"
        :src="collegeEmblem"
        alt="自动化学院院徽"
      />
    </div>

    <div
      class="dashboard-header__title-wrap"
      :style="{
        width: `${CHROME_HEADER_TITLE_WIDTH}px`,
        height: `${CHROME_HEADER_HEIGHT}px`,
        backgroundImage: `url(${headerTitleBg})`,
      }"
    >
      <h1 class="dashboard-header__title dashboard-header__title--sr-only">{{ DASHBOARD_TITLE }}</h1>
    </div>

    <div
      class="dashboard-header__clock"
      :style="{
        top: `${CHROME_CLOCK_INSET_TOP}px`,
        right: `${CHROME_CLOCK_INSET_RIGHT}px`,
      }"
      aria-live="polite"
    >
      <time class="dashboard-header__clock-value">{{ nowLabel }}</time>
    </div>
  </header>
</template>

<style scoped>
.dashboard-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 6;
  height: 129px;
  pointer-events: none;
}

.dashboard-header__brand {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 14px;
  pointer-events: none;
}

.dashboard-header__emblem {
  display: block;
  object-fit: contain;
  filter: drop-shadow(0 0 8px rgba(33, 230, 255, 0.2));
}

.dashboard-header__emblem--university {
  height: 52px;
  width: auto;
  max-width: 188px;
}

.dashboard-header__emblem--college {
  height: 52px;
  width: auto;
  max-width: 54px;
  flex-shrink: 0;
}

.dashboard-header__title-wrap {
  position: absolute;
  left: 50%;
  top: 0;
  transform: translateX(-50%);
  background-repeat: no-repeat;
  background-position: center;
  background-size: 100% 100%;
  pointer-events: none;
}

.dashboard-header__title--sr-only {
  margin: 0;
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.dashboard-header__clock {
  position: absolute;
  display: flex;
  align-items: center;
  pointer-events: auto;
}

.dashboard-header__clock-value {
  color: #6ec8e8;
  font-size: 17px;
  font-weight: 500;
  font-family: 'Consolas', 'Courier New', monospace;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.4px;
  text-shadow: 0 0 10px rgba(33, 230, 255, 0.35);
}

@media (max-width: 1320px) {
  .dashboard-header__brand {
    transform: scale(0.82);
    transform-origin: left top;
  }

  .dashboard-header__clock {
    top: 28px !important;
    right: 40px !important;
  }

  .dashboard-header__clock-value {
    font-size: 14px;
  }
}

@media (max-width: 900px) {
  .dashboard-header__title-wrap {
    width: min(92vw, 775px) !important;
  }

  .dashboard-header__emblem--university {
    max-width: 150px;
  }
}
</style>
