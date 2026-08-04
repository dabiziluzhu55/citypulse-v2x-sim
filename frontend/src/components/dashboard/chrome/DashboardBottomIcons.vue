<script setup lang="ts">
import bottomIconsBg from '../../../assets/design/chrome/bottom-icons.svg?url'
import { useDashboardOverlay } from '../../../composables/useDashboardOverlay'

const { toggleCommunicationPanel } = useDashboardOverlay()
</script>

<template>
  <div class="dashboard-bottom-icons">
    <span class="dashboard-bottom-icons__label dashboard-bottom-icons__label--left">收起面板</span>
    <button
      type="button"
      class="dashboard-bottom-icons__communication"
      title="车路云通信记录"
      aria-label="打开车路云通信记录"
      @click="toggleCommunicationPanel"
    >
      <span class="dashboard-bottom-icons__crop" aria-hidden="true"><img :src="bottomIconsBg" alt="" /></span>
      <strong>车路云通信记录</strong>
    </button>
    <span class="dashboard-bottom-icons__label dashboard-bottom-icons__label--right">暂定</span>
  </div>
</template>

<style scoped>
.dashboard-bottom-icons {
  position: fixed;
  left: 50%;
  bottom: 12px;
  z-index: 8;
  width: 421px;
  height: 94px;
  transform: translateX(-50%);
  color: #d9f5ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  pointer-events: none;
}
.dashboard-bottom-icons__communication {
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 176px;
  height: 94px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #e8f9ff;
  cursor: pointer;
  transform: translateX(-50%);
  pointer-events: auto;
}
.dashboard-bottom-icons__crop {
  position: absolute;
  left: 50%;
  top: 0;
  width: 70px;
  height: 70px;
  overflow: hidden;
  transform: translateX(-50%);
  transition: transform .2s ease, filter .2s ease;
}
.dashboard-bottom-icons__crop img { position: absolute; left: -351px; top: -8px; width: 421px; height: 82px; max-width: none; }
.dashboard-bottom-icons__communication strong,
.dashboard-bottom-icons__label {
  position: absolute;
  bottom: 0;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0;
  text-shadow: 0 0 8px rgba(33, 230, 255, .72);
  white-space: nowrap;
}
.dashboard-bottom-icons__communication strong { left: 50%; transform: translateX(-50%); }
.dashboard-bottom-icons__communication:hover .dashboard-bottom-icons__crop,
.dashboard-bottom-icons__communication:focus-visible .dashboard-bottom-icons__crop { transform: translateX(-50%) translateY(-2px); filter: drop-shadow(0 0 9px #21e6ff); }
.dashboard-bottom-icons__communication:focus-visible { outline: none; }
.dashboard-bottom-icons__label--left { left: 4px; }
.dashboard-bottom-icons__label--right { right: 12px; }
@media (max-width: 1320px) {
  .dashboard-bottom-icons { transform: translateX(-50%) scale(.9); transform-origin: center bottom; }
}
</style>
