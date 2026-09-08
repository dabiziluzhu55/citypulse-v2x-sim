<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import type {
  CollaborationLogEntry,
  V2XLogStatus,
  V2XRole,
} from '../../types/collaboration'
import {
  V2X_DIRECTION_FILTERS,
  V2X_LINK_FILTERS,
  v2xCommunicationEmptyText,
} from '../../utils/v2xCommunication.ts'

const PAGE_SIZE = 8

const props = defineProps<{
  logEntries: CollaborationLogEntry[]
  loading: boolean
  error: string | null
  connected: boolean
  controlMode?: string
}>()

const emit = defineEmits<{ close: [] }>()

const currentPage = ref(1)
const selectedTimeRange = ref<[string, string] | null>(null)
const directionFilter = ref('all')
const linkFilter = ref('all')
const messageFilter = ref('all')
const keyword = ref('')
const autoRefresh = ref(true)
const refreshIntervalSeconds = ref(5)
const displayedEntries = ref<CollaborationLogEntry[]>([...props.logEntries])
let refreshTimer: ReturnType<typeof setInterval> | null = null

const emptyText = computed(() => v2xCommunicationEmptyText(props.controlMode))

const messageOptions = computed(() => {
  const values = new Map<string, string>()
  for (const entry of displayedEntries.value) {
    if (entry.messageType) values.set(entry.messageType, entry.messageTag || entry.messageType)
  }
  return [...values.entries()].map(([value, label]) => ({ value, label }))
})

const filteredRows = computed(() => {
  const search = keyword.value.trim().toLowerCase()
  return displayedEntries.value.filter((row) => {
    const direction = `${row.sourceRole ?? 'unknown'}->${row.destinationRole ?? 'unknown'}`
    const clock = row.timeLabel.slice(0, 8)
    const matchesTime = !selectedTimeRange.value
      || (clock >= selectedTimeRange.value[0] && clock <= selectedTimeRange.value[1])
    const matchesDirection = directionFilter.value === 'all' || direction === directionFilter.value
    const matchesLink = linkFilter.value === 'all' || row.linkType === linkFilter.value
    const matchesMessage = messageFilter.value === 'all' || row.messageType === messageFilter.value
    const searchable = [
      row.source,
      row.destination,
      row.message,
      row.messageTag,
    ].filter(Boolean).join(' ').toLowerCase()
    return matchesTime
      && matchesDirection
      && matchesLink
      && matchesMessage
      && (!search || searchable.includes(search))
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pageRows = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE
  return filteredRows.value.slice(start, start + PAGE_SIZE)
})

function syncEntries(): void {
  displayedEntries.value = [...props.logEntries]
}

function restartAutoRefresh(): void {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
  syncEntries()
  if (autoRefresh.value) {
    refreshTimer = setInterval(syncEntries, refreshIntervalSeconds.value * 1_000)
  }
}

function roleLabel(role?: V2XRole): string {
  if (role === 'vehicle') return '车辆'
  if (role === 'road') return '路口'
  if (role === 'cloud') return '云端'
  return '--'
}

function statusLabel(status?: V2XLogStatus): string {
  if (status === 'failed') return '失败'
  if (status === 'sending') return '发送中'
  return '成功'
}

function csvCell(value: unknown): string {
  return `"${String(value ?? '').replaceAll('"', '""')}"`
}

function communicationExportDate(): string {
  return filteredRows.value.find((row) => row.dateLabel)?.dateLabel
    ?? new Date().toLocaleDateString('sv-SE')
}

function exportCommunicationLog(): void {
  const header = ['时间', '来源', '来源角色', '目标', '目标角色', '链路类型', '消息类型', '内容摘要', '状态']
  const rows = filteredRows.value.map((row) => [
    `${row.dateLabel ?? ''} ${row.timeLabel}`.trim(),
    row.source,
    roleLabel(row.sourceRole),
    row.destination ?? '',
    roleLabel(row.destinationRole),
    row.linkType ?? 'UNKNOWN',
    row.messageTag ?? row.messageType ?? '',
    row.message,
    statusLabel(row.status),
  ])
  const csv = [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `citypulse-v2x-${communicationExportDate()}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

watch([autoRefresh, refreshIntervalSeconds], restartAutoRefresh, { immediate: true })
watch(
  [selectedTimeRange, directionFilter, linkFilter, messageFilter, keyword],
  () => { currentPage.value = 1 },
)
watch(filteredRows, () => {
  if (currentPage.value > totalPages.value) currentPage.value = totalPages.value
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <section class="communication-panel" aria-label="车路云通信记录">
    <button type="button" class="communication-panel__close" title="关闭" aria-label="关闭车路云通信记录" @click="emit('close')">×</button>

    <header class="communication-panel__section-head" aria-hidden="true">
      <strong>时间</strong>
      <strong>通信流</strong>
      <strong>发送信息</strong>
    </header>

    <div class="communication-panel__toolbar">
      <el-time-picker v-model="selectedTimeRange" class="filter-time" is-range format="HH:mm:ss" value-format="HH:mm:ss" range-separator="～" start-placeholder="开始时间" end-placeholder="结束时间" :clearable="true" />
      <el-select v-model="directionFilter" class="filter-direction" aria-label="通信方向">
        <el-option
          v-for="item in V2X_DIRECTION_FILTERS"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <el-select v-model="linkFilter" class="filter-link" aria-label="链路类型">
        <el-option label="全部链路" value="all" />
        <el-option v-for="item in V2X_LINK_FILTERS" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="messageFilter" class="filter-message" aria-label="消息类型">
        <el-option label="全部类型" value="all" />
        <el-option v-for="item in messageOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-input v-model="keyword" class="filter-search" clearable placeholder="搜索 路口 / 车辆 / 消息内容" />
      <el-button class="communication-panel__export" @click="exportCommunicationLog">导出日志</el-button>
    </div>

    <div class="communication-panel__table">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-skeleton v-if="loading && displayedEntries.length === 0" animated :rows="8" />
      <el-table v-else :data="pageRows" stripe height="100%" table-layout="fixed" :empty-text="emptyText" row-key="id">
        <el-table-column prop="timeLabel" label="时间" width="108" />
        <el-table-column label="来源" min-width="200">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <div class="endpoint-cell">{{ row.source }}</div>
          </template>
        </el-table-column>
        <el-table-column
          class-name="flow-arrow-column"
          label-class-name="flow-arrow-header"
          width="56"
          align="center"
        >
          <template #header><span /></template>
          <template #default>
            <span class="flow-arrow" aria-hidden="true">→</span>
          </template>
        </el-table-column>
        <el-table-column label="目标" min-width="200">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <div class="endpoint-cell">{{ row.destination || '--' }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="linkType" label="链路类型" width="96" align="center" />
        <el-table-column label="消息类型" width="120" align="center">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <span class="message-tag" :data-type="row.messageType">{{ row.messageTag || row.messageType || '--' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="内容摘要" min-width="160">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <div class="summary-cell">{{ row.message }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96" align="center">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <span class="status-cell" :class="`is-${row.status ?? 'success'}`"><i />{{ statusLabel(row.status) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <footer class="communication-panel__footer">
      <span>共 {{ filteredRows.length }} 条记录</span>
      <el-pagination v-model:current-page="currentPage" :page-size="PAGE_SIZE" :pager-count="7" :total="filteredRows.length" layout="prev, pager, next" background />
      <em>共 {{ totalPages }} 页</em>
      <div class="communication-panel__refresh">
        <span :class="{ 'is-online': connected }">{{ connected ? '实时连接' : '连接中断' }}</span>
        <label>自动刷新 <el-switch v-model="autoRefresh" /></label>
        <label>刷新间隔
          <el-select v-model="refreshIntervalSeconds">
            <el-option label="2s" :value="2" />
            <el-option label="5s" :value="5" />
            <el-option label="10s" :value="10" />
          </el-select>
        </label>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.communication-panel {
  position: relative;
  display: grid;
  grid-template-rows: 68px 74px minmax(0, 1fr) 64px;
  width: min(1490px, calc(100vw - 48px));
  height: min(820px, calc(100vh - 64px));
  min-height: 620px;
  padding: 0 22px 12px;
  box-sizing: border-box;
  border: 1px solid rgba(46, 151, 225, .55);
  background: linear-gradient(180deg, rgba(7, 46, 86, .97), rgba(3, 30, 62, .98));
  box-shadow: inset 0 0 42px rgba(30, 126, 219, .12), 0 18px 52px rgba(0, 8, 30, .45);
  color: #f4fbff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  overflow: hidden;
  pointer-events: auto;
}
.communication-panel__close { position: absolute; top: -2px; right: -2px; z-index: 3; width: 28px; height: 28px; padding: 0; border: 1px solid rgba(98,216,255,.42); background: rgba(3,29,61,.9); color: #dff6ff; font-size: 20px; cursor: pointer; }
.communication-panel__section-head { display: grid; grid-template-columns: repeat(3, 1fr); align-items: center; margin: 0; padding-right: 30px; background: linear-gradient(180deg, rgba(29,89,147,.9), rgba(19,69,123,.82)); color: #fff; font-size: 22px; letter-spacing: .08em; text-align: center; }
.communication-panel__toolbar { display: grid; grid-template-columns: minmax(230px,1.3fr) repeat(3,minmax(120px,.72fr)) minmax(240px,1.45fr) 112px; gap: 10px; align-items: center; min-width: 0; padding: 14px 20px; border-bottom: 1px solid rgba(69,162,226,.22); }
.communication-panel__toolbar > * { min-width: 0; }
.filter-time { width: 100%; min-width: 0; }
.communication-panel__toolbar :deep(.el-input__wrapper),
.communication-panel__toolbar :deep(.el-select__wrapper) { background: rgba(3,35,68,.78); box-shadow: 0 0 0 1px rgba(72,163,225,.36) inset; }
.communication-panel__toolbar :deep(.el-input__inner),
.communication-panel__toolbar :deep(.el-select__placeholder),
.communication-panel__toolbar :deep(.el-range-input),
.communication-panel__toolbar :deep(.el-range-separator) { color: #dff5ff; }
.communication-panel__export { border-color: rgba(72,163,225,.48); background: rgba(6,47,89,.84); color: #dff5ff; }
.communication-panel__table { min-height: 0; }
.communication-panel__table :deep(.el-table) { --el-table-border-color: rgba(68,151,211,.18); --el-table-bg-color: transparent; --el-table-tr-bg-color: transparent; --el-fill-color-lighter: rgba(31,93,148,.2); --el-table-row-hover-bg-color: rgba(33,230,255,.08); background: transparent; color: #eef9ff; font-size: 14px; }
.communication-panel__table :deep(.el-table::before), .communication-panel__table :deep(.el-table__inner-wrapper::before) { display: none; }
.communication-panel__table :deep(th.el-table__cell) { height: 50px; padding: 0; background: rgba(8,55,96,.94); color: #bfe6fb; font-weight: 600; }
.communication-panel__table :deep(td.el-table__cell) { height: 62px; padding: 0; border-bottom: 1px solid rgba(68,151,211,.18); background: transparent; }
.communication-panel__table :deep(.el-table__body tr.el-table__row--striped td.el-table__cell) { background: rgba(15,69,116,.18); }
.communication-panel__table :deep(.flow-arrow-header .cell) { padding: 0; visibility: hidden; }
.communication-panel__table :deep(td.flow-arrow-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 0;
}
.endpoint-cell, .summary-cell {
  overflow: hidden;
  color: #eaf8ff;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.flow-arrow {
  display: block;
  color: #28baf6;
  font-size: 22px;
  line-height: 1;
  text-shadow: 0 0 8px rgba(40,186,246,.5);
}
.message-tag { display: inline-block; max-width: 112px; padding: 4px 9px; overflow: hidden; border: 1px solid rgba(43,172,255,.35); border-radius: 4px; background: rgba(18,93,177,.58); color: #dff6ff; text-overflow: ellipsis; white-space: nowrap; }
.message-tag[data-type='SPaTV2'] { border-color: rgba(30,214,154,.38); background: rgba(8,126,91,.55); }
.message-tag[data-type='RegionalPriorityV1'] { border-color: rgba(255,190,52,.42); background: rgba(142,93,12,.62); }
.message-tag[data-type='IntersectionSummaryV1'] { border-color: rgba(168,119,255,.42); background: rgba(83,56,151,.62); }
.message-tag[data-type='MAPV1'] { border-color: rgba(120,196,255,.42); background: rgba(18,86,148,.62); }
.status-cell { display: inline-flex; align-items: center; gap: 8px; color: #8de0a1; }
.status-cell i { width: 8px; height: 8px; border-radius: 50%; background: #62d776; box-shadow: 0 0 7px rgba(98,215,118,.62); }
.status-cell.is-failed { color: #ff9d9d; }
.status-cell.is-failed i { background: #ff6464; }
.status-cell.is-sending { color: #ffd47b; }
.status-cell.is-sending i { background: #ffbd3f; }
.communication-panel__footer { display: grid; grid-template-columns: 180px minmax(300px,auto) 100px 1fr; gap: 16px; align-items: center; color: #a7cce1; font-size: 13px; }
.communication-panel__footer em { font-style: normal; }
.communication-panel__footer :deep(.el-pagination) { --el-pagination-bg-color: rgba(5,51,92,.88); --el-pagination-button-color: #eefaff; --el-pagination-hover-color: #21e6ff; justify-content: center; }
.communication-panel__refresh { display: flex; align-items: center; justify-content: flex-end; gap: 18px; }
.communication-panel__refresh > span::before { content: ''; display: inline-block; width: 7px; height: 7px; margin-right: 6px; border-radius: 50%; background: #ff6969; }
.communication-panel__refresh > span.is-online::before { background: #62d776; }
.communication-panel__refresh label { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.communication-panel__refresh :deep(.el-select) { width: 82px; }

@media (max-width: 1380px) {
  .communication-panel { grid-template-rows: 58px 126px minmax(0,1fr) 64px; min-height: 560px; }
  .communication-panel__toolbar {
    grid-template-columns: repeat(6,minmax(0,1fr));
    grid-template-areas:
      "time time time time time time"
      "direction link message search search export";
    gap: 8px;
    padding: 10px 14px;
  }
  .filter-time { grid-area: time; }
  .filter-direction { grid-area: direction; }
  .filter-link { grid-area: link; }
  .filter-message { grid-area: message; }
  .filter-search { grid-area: search; }
  .communication-panel__export { grid-area: export; }
  .communication-panel__footer { grid-template-columns: 120px 1fr 80px; }
  .communication-panel__refresh { display: none; }
}
</style>
