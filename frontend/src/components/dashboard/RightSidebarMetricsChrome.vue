<script setup lang="ts">
import {
  RIGHT_SIDEBAR_CHARTS,
  RIGHT_SIDEBAR_COMMUNICATION_TABLE,
  RIGHT_SIDEBAR_CONTENT_HEIGHT,
  RIGHT_SIDEBAR_CONTENT_WIDTH,
  RIGHT_SIDEBAR_EXPORT_BUTTON,
} from '../../constants/rightSidebarLayout'

const tableHead = RIGHT_SIDEBAR_COMMUNICATION_TABLE.head
const exportBtn = RIGHT_SIDEBAR_EXPORT_BUTTON
const charts = RIGHT_SIDEBAR_CHARTS
</script>

<template>
  <div class="rs-metrics-chrome" aria-hidden="true">
    <!-- 通信表头渐变条 -->
    <svg
      class="rs-metrics-chrome__table-head"
      :style="{
        left: `calc(${tableHead.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        top: `calc(${tableHead.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
        width: `calc(${tableHead.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        height: `calc(${tableHead.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      }"
      :viewBox="`0 0 ${tableHead.width} ${tableHead.height}`"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id="rs-table-head-fill" x1="0" y1="0" x2="368" y2="40" gradientUnits="userSpaceOnUse">
          <stop stop-color="#1B4A82" stop-opacity="0.72" />
          <stop offset="0.55" stop-color="#0E2F58" stop-opacity="0.58" />
          <stop offset="1" stop-color="#081E36" stop-opacity="0.42" />
        </linearGradient>
      </defs>
      <rect width="100%" height="100%" fill="url(#rs-table-head-fill)" />
      <rect width="100%" height="100%" fill="none" stroke="rgba(33,230,255,0.38)" stroke-width="1" />
      <line x1="0" y1="1" :x2="tableHead.width" y2="1" stroke="rgba(157,212,255,0.45)" stroke-width="1" />
    </svg>

    <!-- 指标区分隔带 -->
    <div
      class="rs-metrics-chrome__separator-band"
      :style="{
        left: `calc(${charts.separatorBand.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        top: `calc(${charts.separatorBand.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
        width: `calc(${charts.separatorBand.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        height: `calc(${charts.separatorBand.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      }"
    />

    <!-- 导出按钮描边框 -->
    <svg
      class="rs-metrics-chrome__export-frame"
      :style="{
        left: `calc(${exportBtn.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        top: `calc(${exportBtn.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
        width: `calc(${exportBtn.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        height: `calc(${exportBtn.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      }"
      :viewBox="`0 0 ${exportBtn.width} ${exportBtn.height}`"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id="rs-export-stroke-a" x1="0" y1="0" x2="103" y2="32" gradientUnits="userSpaceOnUse">
          <stop stop-color="#21E6FF" stop-opacity="0.65" />
          <stop offset="1" stop-color="#247CFF" stop-opacity="0.2" />
        </linearGradient>
        <linearGradient id="rs-export-stroke-b" x1="103" y1="0" x2="0" y2="32" gradientUnits="userSpaceOnUse">
          <stop stop-color="#00A3FF" stop-opacity="0.45" />
          <stop offset="1" stop-color="#21E6FF" stop-opacity="0.1" />
        </linearGradient>
      </defs>
      <rect
        x="0.5"
        y="0.5"
        :width="exportBtn.width - 1"
        :height="exportBtn.height - 1"
        :rx="exportBtn.rx"
        fill="rgba(4,22,40,0.55)"
        stroke="url(#rs-export-stroke-a)"
        stroke-opacity="0.5"
      />
      <rect
        x="0.5"
        y="0.5"
        :width="exportBtn.width - 1"
        :height="exportBtn.height - 1"
        :rx="exportBtn.rx"
        fill="none"
        stroke="url(#rs-export-stroke-b)"
        stroke-opacity="0.5"
      />
    </svg>

    <!-- 图表 1 装饰 -->
    <svg
      class="rs-metrics-chrome__chart rs-metrics-chrome__chart--queue"
      :style="{
        left: `calc(${charts.queue.plot.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        top: `calc(${charts.queue.plot.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
        width: `calc(${charts.queue.plot.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        height: `calc(${charts.queue.plot.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      }"
      :viewBox="`0 0 ${charts.queue.plot.width} ${charts.queue.plot.height}`"
      preserveAspectRatio="none"
    >
      <path
        v-for="(lineY, index) in charts.queue.gridLines"
        :key="`q-grid-${index}`"
        :d="`M17 ${lineY - charts.queue.plot.top}H${charts.queue.plot.width}`"
        stroke="#B0D7FF"
        stroke-opacity="0.25"
        stroke-dasharray="6"
      />
      <rect
        x="17"
        :y="charts.queue.separatorY - charts.queue.plot.top"
        :width="charts.queue.plot.width - 17"
        height="0.76"
        fill="#B0D7FF"
        fill-opacity="0.4"
      />
    </svg>

    <!-- 图表 2 装饰 -->
    <svg
      class="rs-metrics-chrome__chart rs-metrics-chrome__chart--waiting"
      :style="{
        left: `calc(${charts.waiting.plot.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        top: `calc(${charts.waiting.plot.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
        width: `calc(${charts.waiting.plot.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
        height: `calc(${charts.waiting.plot.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      }"
      :viewBox="`0 0 ${charts.waiting.plot.width} ${charts.waiting.plot.height}`"
      preserveAspectRatio="none"
    >
      <path
        v-for="(lineY, index) in charts.waiting.gridLines"
        :key="`w-grid-${index}`"
        :d="`M17 ${lineY - charts.waiting.plot.top}H${charts.waiting.plot.width}`"
        stroke="#B0D7FF"
        stroke-opacity="0.25"
        stroke-dasharray="6"
      />
      <rect
        x="17"
        :y="charts.waiting.separatorY - charts.waiting.plot.top"
        :width="charts.waiting.plot.width - 17"
        height="0.76"
        fill="#B0D7FF"
        fill-opacity="0.4"
      />
    </svg>
  </div>
</template>

<style scoped>
.rs-metrics-chrome {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
}

.rs-metrics-chrome__table-head,
.rs-metrics-chrome__export-frame,
.rs-metrics-chrome__chart {
  position: absolute;
}

.rs-metrics-chrome__separator-band {
  position: absolute;
  background: radial-gradient(ellipse at center, rgba(33, 230, 255, 0.22) 0%, transparent 72%);
  opacity: 0.85;
}

.rs-metrics-chrome__chart {
  overflow: visible;
}
</style>
