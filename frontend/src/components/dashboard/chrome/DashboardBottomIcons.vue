<script setup lang="ts">
import bottomNavigation from '../../../assets/design/ai-control/bottom-navigation.svg?url'
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
    <img
      class="dashboard-bottom-icons__art"
      :src="bottomNavigation"
      alt=""
      aria-hidden="true"
    />

    <button
      type="button"
      class="dashboard-bottom-icons__hit dashboard-bottom-icons__hit--ai"
      :class="{ 'is-active': aiControlPanelOpen }"
      :aria-pressed="aiControlPanelOpen"
      aria-label="打开AI管控模型"
      title="AI管控模型"
      @click="toggleAiControlPanel"
    >
      <span class="dashboard-bottom-icons__sr-only">AI管控模型</span>
    </button>

    <button
      type="button"
      class="dashboard-bottom-icons__hit dashboard-bottom-icons__hit--communication"
      :class="{ 'is-active': communicationPanelOpen }"
      :aria-pressed="communicationPanelOpen"
      aria-label="打开车路云通信记录"
      title="车路云通信记录"
      @click="toggleCommunicationPanel"
    >
      <span class="dashboard-bottom-icons__sr-only">车路云通信记录</span>
    </button>

    <button
      type="button"
      class="dashboard-bottom-icons__hit dashboard-bottom-icons__hit--roadside"
      :class="{ 'is-active': roadsideDevicePanelOpen }"
      :aria-pressed="roadsideDevicePanelOpen"
      aria-label="打开路侧设备面板"
      title="路侧设备"
      @click="toggleRoadsideDevicePanel"
    >
      <span class="dashboard-bottom-icons__sr-only">路侧设备</span>
    </button>
  </nav>
</template>

<style scoped>
.dashboard-bottom-icons {
  position: fixed;
  left: 50%;
  bottom: var(--dashboard-bottom-icons-offset-y, 0);
  z-index: 8;
  width: 571px;
  height: 123px;
  transform: translateX(-50%);
  transform-origin: center bottom;
  pointer-events: none;
}

.dashboard-bottom-icons__art {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  pointer-events: none;
}

.dashboard-bottom-icons__hit {
  position: absolute;
  z-index: 2;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  pointer-events: auto;
}

.dashboard-bottom-icons__hit::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 4px;
  width: calc(100% - 14px);
  height: 34px;
  border-radius: 17px;
  opacity: 0;
  transform: translateX(-50%);
  background: rgba(16, 111, 214, .1);
  box-shadow:
    inset 0 0 10px rgba(82, 194, 250, .22),
    0 0 16px rgba(33, 230, 255, .62);
  transition: opacity .2s ease;
  pointer-events: none;
}

.dashboard-bottom-icons__hit:hover::after,
.dashboard-bottom-icons__hit:focus-visible::after,
.dashboard-bottom-icons__hit.is-active::after { opacity: 1; }

.dashboard-bottom-icons__hit:focus-visible {
  outline: 1px solid rgba(82, 194, 250, .72);
  outline-offset: -4px;
}

.dashboard-bottom-icons__hit--ai {
  left: 62px;
  bottom: 0;
  width: 168px;
  height: 46px;
}

.dashboard-bottom-icons__hit--communication {
  left: 217px;
  top: 0;
  width: 140px;
  height: 123px;
}

.dashboard-bottom-icons__hit--roadside {
  left: 357px;
  bottom: 0;
  width: 154px;
  height: 46px;
}

.dashboard-bottom-icons__sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

@media (max-width: 1320px) {
  .dashboard-bottom-icons { transform: translateX(-50%) scale(.9); }
}

@media (max-width: 620px) {
  .dashboard-bottom-icons { transform: translateX(-50%) scale(.78); }
}
</style>
