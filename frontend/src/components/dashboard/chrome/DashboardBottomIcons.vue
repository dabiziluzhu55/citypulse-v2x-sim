<script setup lang="ts">
import bottomIconsBg from '../../../assets/design/chrome/bottom-icons.svg?url'
import {
  CHROME_BOTTOM_ICONS_HEIGHT,
  CHROME_BOTTOM_ICONS_WIDTH,
} from '../../../constants/dashboardChromeLayout'

const iconItems = [
  {
    key: 'settings',
    label: '系统设置',
    title: '系统设置',
    action: () => {},
  },
  {
    key: 'schedule',
    label: '仿真日程',
    title: '仿真计划与运行日程',
    action: () => {},
  },
  {
    key: 'report',
    label: '评估报告',
    title: '量化评估报告导出',
    action: () => {},
  },
] as const
</script>

<template>
  <div class="dashboard-bottom-icons">
    <img
      class="dashboard-bottom-icons__art"
      :src="bottomIconsBg"
      :width="CHROME_BOTTOM_ICONS_WIDTH"
      :height="CHROME_BOTTOM_ICONS_HEIGHT"
      alt=""
      aria-hidden="true"
    />
    <button
      v-for="(item, index) in iconItems"
      :key="item.key"
      type="button"
      class="dashboard-bottom-icons__btn"
      :class="`dashboard-bottom-icons__btn--${index}`"
      :title="item.title"
      :aria-label="item.label"
      @click="item.action"
    />
  </div>
</template>

<style scoped>
.dashboard-bottom-icons {
  position: fixed;
  left: 50%;
  bottom: var(--dashboard-bottom-icons-offset-y, 20px);
  width: 421px;
  height: 82px;
  transform: translateX(-50%);
  z-index: 7;
  pointer-events: none;
}

.dashboard-bottom-icons__art {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: fill;
  pointer-events: none;
}

.dashboard-bottom-icons__btn {
  position: absolute;
  width: 70px;
  height: 70px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  pointer-events: auto;
  transition: filter 0.2s ease, transform 0.2s ease;
}

.dashboard-bottom-icons__btn:hover {
  transform: translateY(-2px);
  filter: drop-shadow(0 0 10px rgba(33, 230, 255, 0.65));
}

.dashboard-bottom-icons__btn--0 {
  left: 0;
  bottom: 0;
}

.dashboard-bottom-icons__btn--1 {
  left: 50%;
  bottom: 10px;
  transform: translateX(-50%);
}

.dashboard-bottom-icons__btn--1:hover {
  transform: translateX(-50%) translateY(-2px);
}

.dashboard-bottom-icons__btn--2 {
  right: 0;
  bottom: 2px;
}

@media (max-width: 1320px) {
  .dashboard-bottom-icons {
    transform: translateX(-50%) scale(0.9);
    transform-origin: center bottom;
  }
}
</style>
