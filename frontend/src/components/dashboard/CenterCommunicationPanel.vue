<script setup lang="ts">
import {
  formatCommunicationFlowParts,
  formatLogClock,
} from '../../constants/rightSidebarOptions'
import type { CollaborationLogEntry } from '../../types/collaboration'

const props = defineProps<{
  logEntries: CollaborationLogEntry[]
  loading: boolean
  error: string | null
  connected: boolean
}>()

const emit = defineEmits<{
  close: []
}>()
</script>

<template>
  <section class="communication-panel" aria-label="车路云通信记录">
    <header class="communication-panel__header">
      <div class="communication-panel__title-wrap">
        <span class="communication-panel__eyebrow">V2X MESSAGE STREAM</span>
        <h2 class="communication-panel__title">车路云通信记录</h2>
      </div>
      <div class="communication-panel__actions">
        <div class="communication-panel__status" :class="{ 'is-online': connected }">
          <span class="communication-panel__status-dot" aria-hidden="true" />
          {{ connected ? '实时连接' : '等待连接' }}
        </div>
        <button
          type="button"
          class="communication-panel__close"
          aria-label="关闭车路云通信记录"
          title="关闭"
          @click="emit('close')"
        >
          <span aria-hidden="true" />
        </button>
      </div>
    </header>

    <div class="communication-panel__table-head">
      <span>时间</span>
      <span>通信流</span>
      <span>发送信息</span>
    </div>

    <div class="communication-panel__body">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="communication-panel__alert"
      />

      <el-skeleton v-if="loading && logEntries.length === 0" animated :rows="3" />

      <template v-else>
        <div
          v-for="entry in props.logEntries"
          :key="entry.id"
          class="communication-panel__row"
        >
          <span class="communication-panel__time">{{ formatLogClock(entry.timeLabel) }}</span>
          <span class="communication-panel__flow">
            <span>{{ formatCommunicationFlowParts(entry)[0] }}</span>
            <span class="communication-panel__arrow" aria-hidden="true" />
            <span>{{ formatCommunicationFlowParts(entry)[1] }}</span>
          </span>
          <span class="communication-panel__message" :title="entry.message">{{ entry.message }}</span>
        </div>
        <p v-if="logEntries.length === 0" class="communication-panel__empty">暂无通信记录</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.communication-panel {
  position: relative;
  width: min(100%, 760px);
  height: min(390px, calc(100vh - 300px));
  min-height: 248px;
  padding: 18px 20px 16px;
  color: #eaf8ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  pointer-events: auto;
  background:
    linear-gradient(180deg, rgba(8, 34, 68, 0.9), rgba(2, 15, 34, 0.88)),
    radial-gradient(circle at 50% 0, rgba(33, 230, 255, 0.12), transparent 58%);
  border: 1px solid rgba(67, 178, 255, 0.42);
  clip-path: polygon(18px 0, calc(100% - 18px) 0, 100% 18px, 100% 100%, 0 100%, 0 18px);
  box-shadow: inset 0 0 24px rgba(33, 139, 255, 0.08), 0 0 24px rgba(15, 104, 216, 0.12);
  overflow: hidden;
}

.communication-panel::before,
.communication-panel::after {
  content: '';
  position: absolute;
  top: 0;
  width: 34%;
  height: 2px;
  background: linear-gradient(90deg, transparent, #62d8ff);
  box-shadow: 0 0 8px rgba(98, 216, 255, 0.7);
}

.communication-panel::before { left: 0; }
.communication-panel::after { right: 0; transform: scaleX(-1); }

.communication-panel__header {
  height: 50px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  border-bottom: 1px solid rgba(119, 193, 255, 0.14);
}

.communication-panel__title-wrap {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.communication-panel__eyebrow {
  color: rgba(86, 175, 255, 0.7);
  font-size: 10px;
  letter-spacing: 0.18em;
}

.communication-panel__title {
  margin: 0;
  color: #fff;
  font-size: 19px;
  letter-spacing: 0.08em;
  text-shadow: 0 0 12px rgba(74, 191, 255, 0.5);
}

.communication-panel__actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.communication-panel__status {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #7595ad;
  font-size: 12px;
}

.communication-panel__status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #5c7487;
}

.communication-panel__status.is-online { color: #62e9ff; }
.communication-panel__status.is-online .communication-panel__status-dot {
  background: #62e9ff;
  box-shadow: 0 0 9px #21e6ff;
}

.communication-panel__close {
  position: relative;
  width: 28px;
  height: 28px;
  border: 1px solid rgba(98, 216, 255, 0.34);
  border-radius: 50%;
  background: rgba(4, 31, 61, 0.72);
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.communication-panel__close span::before,
.communication-panel__close span::after {
  content: '';
  position: absolute;
  left: 8px;
  top: 13px;
  width: 11px;
  height: 1px;
  background: #a9dfff;
}

.communication-panel__close span::before { transform: rotate(45deg); }
.communication-panel__close span::after { transform: rotate(-45deg); }

.communication-panel__close:hover,
.communication-panel__close:focus-visible {
  border-color: #62d8ff;
  background: rgba(33, 139, 255, 0.16);
  box-shadow: 0 0 10px rgba(33, 230, 255, 0.38);
  outline: none;
}

.communication-panel__table-head,
.communication-panel__row {
  display: grid;
  grid-template-columns: 92px 138px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.communication-panel__table-head {
  height: 34px;
  padding: 0 10px;
  color: #8ec8ef;
  font-size: 12px;
  font-weight: 600;
  background: linear-gradient(90deg, rgba(29, 88, 151, 0.46), rgba(15, 47, 87, 0.12));
}

.communication-panel__body {
  height: calc(100% - 84px);
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(52, 159, 232, 0.5) transparent;
}

.communication-panel__row {
  min-height: 32px;
  padding: 6px 10px;
  border-bottom: 1px solid rgba(138, 196, 238, 0.07);
  font-size: 12px;
}

.communication-panel__row:hover { background: rgba(33, 230, 255, 0.04); }
.communication-panel__time { color: #d9f4ff; font-variant-numeric: tabular-nums; }
.communication-panel__flow { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.communication-panel__arrow {
  width: 0;
  height: 0;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
  border-left: 7px solid #ffe47a;
}
.communication-panel__message { overflow: hidden; color: #bcd9e9; text-overflow: ellipsis; white-space: nowrap; }
.communication-panel__empty { margin: 42px 0 0; color: #668ca7; text-align: center; }
.communication-panel__alert { margin: 6px; }

@media (max-height: 820px) {
  .communication-panel { height: 210px; }
  .communication-panel__body { height: 92px; }
}
</style>
