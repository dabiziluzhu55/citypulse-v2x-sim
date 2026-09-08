<script setup lang="ts">
import { useDashboardOverlay } from '../../../composables/useDashboardOverlay'

const {
  aiControlPanelOpen,
  communicationPanelOpen,
  roadsideDevicePanelOpen,
  toggleAiControlPanel,
  toggleCommunicationPanel,
  toggleRoadsideDevicePanel,
} = useDashboardOverlay()
</script>

<template>
  <nav class="dashboard-bottom-icons" aria-label="底部功能导航">
    <button
      type="button"
      class="bottom-nav-item bottom-nav-item--ai"
      :class="{ 'is-active': aiControlPanelOpen }"
      :aria-pressed="aiControlPanelOpen"
      aria-label="打开AI管控模型"
      title="AI管控模型"
      @click="toggleAiControlPanel"
    >
      <span class="bottom-nav-item__label">AI管控模型</span>
    </button>

    <button
      type="button"
      class="bottom-nav-item bottom-nav-item--communication"
      :class="{ 'is-active': communicationPanelOpen }"
      :aria-pressed="communicationPanelOpen"
      aria-label="打开车路云通信记录"
      title="车路云通信记录"
      @click="toggleCommunicationPanel"
    >
      <span class="bottom-nav-item__label">车路云通信记录</span>
    </button>

    <button
      type="button"
      class="bottom-nav-item bottom-nav-item--roadside"
      :class="{ 'is-active': roadsideDevicePanelOpen }"
      :aria-pressed="roadsideDevicePanelOpen"
      aria-label="打开路侧设备画面"
      title="路侧设备画面"
      @click="toggleRoadsideDevicePanel"
    >
      <span class="bottom-nav-item__label">路侧设备画面</span>
    </button>
  </nav>
</template>

<style scoped>
.dashboard-bottom-icons {
  position: fixed;
  left: 50%;
  bottom: var(--dashboard-bottom-icons-offset-y, 0);
  z-index: 8;
  display: grid;
  grid-template-columns: 1fr 1.4fr 1fr;
  align-items: stretch;
  width: 571px;
  height: 46px;
  padding: 0 28px;
  box-sizing: border-box;
  transform: translateX(-50%);
  transform-origin: center bottom;
  pointer-events: none;
}

.bottom-nav-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 46px;
  min-height: 46px;
  padding: 0 10px;
  border: 0;
  background: transparent;
  cursor: pointer;
  pointer-events: auto;
}

.bottom-nav-item::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 8px;
  background: rgba(16, 111, 214, .18);
  box-shadow:
    inset 0 0 10px rgba(82, 194, 250, .22),
    0 0 14px rgba(33, 230, 255, .36);
  opacity: 0;
  pointer-events: none;
  transition: opacity .2s ease, background .2s ease, box-shadow .2s ease;
}

.bottom-nav-item:hover::before,
.bottom-nav-item:focus-visible::before {
  opacity: 1;
}

.bottom-nav-item.is-active::before {
  opacity: 1;
  background: rgba(16, 111, 214, .3);
  box-shadow:
    inset 0 0 0 1px rgba(125, 211, 255, .42),
    inset 0 0 12px rgba(82, 194, 250, .3),
    0 0 18px rgba(33, 230, 255, .5);
}

.bottom-nav-item:focus-visible {
  outline: 1px solid rgba(82, 194, 250, .72);
  outline-offset: -3px;
}

.bottom-nav-item__label {
  position: relative;
  z-index: 1;
  overflow: hidden;
  color: #f4fbff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 15px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: .04em;
  text-overflow: ellipsis;
  text-shadow: 0 0 8px rgba(49, 190, 255, .45);
  white-space: nowrap;
}

@media (max-width: 1320px) {
  .dashboard-bottom-icons { transform: translateX(-50%) scale(.9); }
}

@media (max-width: 620px) {
  .dashboard-bottom-icons { transform: translateX(-50%) scale(.78); }
}
</style>
