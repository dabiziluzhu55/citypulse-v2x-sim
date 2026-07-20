<script setup lang="ts">
import bottomCenterBg from '../../../assets/design/chrome/bottom-center.svg?url'
import { useDashboardOverlay } from '../../../composables/useDashboardOverlay'
import {
  CHROME_BOTTOM_CENTER_HEIGHT,
  CHROME_BOTTOM_CENTER_WIDTH,
} from '../../../constants/dashboardChromeLayout'

const { communicationPanelOpen, toggleCommunicationPanel } = useDashboardOverlay()
</script>

<template>
  <div
    class="dashboard-bottom-center"
    :class="{ 'is-active': communicationPanelOpen }"
  >
    <img
      class="dashboard-bottom-center__art"
      :src="bottomCenterBg"
      :width="CHROME_BOTTOM_CENTER_WIDTH"
      :height="CHROME_BOTTOM_CENTER_HEIGHT"
      alt=""
      aria-hidden="true"
    />
    <button
      type="button"
      class="dashboard-bottom-center__trigger"
      :aria-label="communicationPanelOpen ? '关闭车路云通信记录' : '打开车路云通信记录'"
      aria-controls="center-communication-dialog"
      :aria-expanded="communicationPanelOpen"
      title="车路云通信记录"
      @click="toggleCommunicationPanel"
    >
      <span class="dashboard-bottom-center__trigger-label">通信记录</span>
    </button>
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

.dashboard-bottom-center__art {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
  pointer-events: none;
  transition: filter 0.22s ease;
}

.dashboard-bottom-center__trigger {
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 188px;
  height: 54px;
  padding: 15px 24px 6px;
  border: 0;
  background: transparent;
  color: rgba(210, 241, 255, 0.74);
  font: 600 12px/1 'PingFang SC', 'Microsoft YaHei', sans-serif;
  letter-spacing: 0.18em;
  text-shadow: 0 0 8px rgba(33, 230, 255, 0.45);
  transform: translateX(-50%);
  cursor: pointer;
  pointer-events: auto;
  clip-path: polygon(16% 0, 84% 0, 100% 100%, 0 100%);
  transition: color 0.2s ease, text-shadow 0.2s ease;
}

.dashboard-bottom-center__trigger::before {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 5px;
  width: 78px;
  height: 3px;
  border-radius: 50%;
  background: #51d9ff;
  box-shadow: 0 0 8px #21e6ff, 0 0 18px rgba(33, 230, 255, 0.52);
  opacity: 0;
  transform: translateX(-50%) scaleX(0.45);
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.dashboard-bottom-center__trigger:hover,
.dashboard-bottom-center__trigger:focus-visible,
.dashboard-bottom-center.is-active .dashboard-bottom-center__trigger {
  color: #f4fcff;
  text-shadow: 0 0 10px #21e6ff;
  outline: none;
}

.dashboard-bottom-center__trigger:hover::before,
.dashboard-bottom-center__trigger:focus-visible::before,
.dashboard-bottom-center.is-active .dashboard-bottom-center__trigger::before {
  opacity: 1;
  transform: translateX(-50%) scaleX(1);
}

.dashboard-bottom-center.is-active .dashboard-bottom-center__art,
.dashboard-bottom-center:has(.dashboard-bottom-center__trigger:hover) .dashboard-bottom-center__art {
  filter: drop-shadow(0 0 8px rgba(33, 230, 255, 0.62));
}

@media (max-width: 1320px) {
  .dashboard-bottom-center {
    transform: translateX(-50%) scale(0.9);
    transform-origin: center bottom;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dashboard-bottom-center__art,
  .dashboard-bottom-center__trigger,
  .dashboard-bottom-center__trigger::before {
    transition: none;
  }
}
</style>
