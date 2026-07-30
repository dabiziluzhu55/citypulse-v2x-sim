<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  formatCommunicationFlowParts,
  formatLogClock,
} from '../../constants/rightSidebarOptions'
import type { CollaborationLogEntry } from '../../types/collaboration'

const PAGE_SIZE = 6
const props = defineProps<{
  logEntries: CollaborationLogEntry[]
  loading: boolean
  error: string | null
  connected: boolean
}>()
const emit = defineEmits<{ close: [] }>()
const currentPage = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(props.logEntries.length / PAGE_SIZE)))
const pageRows = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE
  return props.logEntries.slice(start, start + PAGE_SIZE)
})

watch(() => props.logEntries.length, () => {
  if (currentPage.value > totalPages.value) currentPage.value = totalPages.value
})
</script>

<template>
  <section class="communication-panel" aria-label="车路云通信记录">
    <header class="communication-panel__header">
      <button type="button" class="communication-panel__close" title="关闭" aria-label="关闭车路云通信记录" @click="emit('close')">×</button>
    </header>

    <div class="communication-panel__table">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-skeleton v-if="loading && logEntries.length === 0" animated :rows="6" />
      <el-table
        v-else
        :data="pageRows"
        stripe
        height="390"
        table-layout="fixed"
        empty-text="暂无可验证的通信记录"
        row-key="id"
      >
        <el-table-column label="时间" width="220" align="center">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <span class="communication-panel__time">{{ formatLogClock(row.timeLabel) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="通信流" width="280" align="center">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <span class="communication-panel__flow">
              <b>{{ formatCommunicationFlowParts(row)[0] }}</b>
              <i aria-hidden="true" />
              <b>{{ formatCommunicationFlowParts(row)[1] }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="发送信息" min-width="420" align="center">
          <template #default="{ row }: { row: CollaborationLogEntry }">
            <span class="communication-panel__message" :title="row.message">{{ row.message }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <footer class="communication-panel__footer">
      <span>共{{ logEntries.length }}条记录</span>
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="PAGE_SIZE"
        :pager-count="7"
        :total="logEntries.length"
        layout="prev, pager, next"
        background
      />
      <em>共{{ totalPages }}页</em>
    </footer>
  </section>
</template>

<style scoped>
.communication-panel {
  position: relative;
  width: min(1000px, calc(100vw - 48px));
  height: min(582px, calc(100vh - 120px));
  min-height: 510px;
  padding: 20px 38px 18px;
  border: 1px solid rgba(91, 159, 255, .72);
  clip-path: polygon(18px 0, 35% 0, 37% 22px, 63% 22px, 65% 0, calc(100% - 18px) 0, 100% 18px, 100% calc(100% - 18px), calc(100% - 18px) 100%, 18px 100%, 0 calc(100% - 18px), 0 18px);
  background: linear-gradient(180deg, rgba(20, 48, 89, .97), rgba(24, 70, 125, .96));
  box-shadow: inset 0 0 42px rgba(69, 136, 225, .18), 0 0 26px rgba(18, 110, 218, .24);
  color: #f4fbff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  overflow: hidden;
  pointer-events: auto;
}
.communication-panel::before,
.communication-panel::after {
  content: '';
  position: absolute;
  top: 0;
  width: 35%;
  height: 2px;
  background: linear-gradient(90deg, transparent, #5ad9ff);
  box-shadow: 0 0 8px rgba(90, 217, 255, .85);
}
.communication-panel::before { left: 0; }
.communication-panel::after { right: 0; transform: scaleX(-1); }
.communication-panel__header { height: 30px; display: flex; align-items: flex-start; justify-content: flex-end; }
.communication-panel__heading { display: flex; align-items: center; gap: 10px; }
.communication-panel__heading > i { width: 4px; height: 18px; background: #21e6ff; box-shadow: 0 0 8px #21e6ff; }
.communication-panel__heading strong { font-size: 18px; letter-spacing: 0; }
.communication-panel__heading span { color: #9bb5c8; font-size: 11px; }
.communication-panel__heading span::before { content: ''; display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: #8395a2; }
.communication-panel__heading span.is-online { color: #65e8ff; }
.communication-panel__heading span.is-online::before { background: #21e6ff; box-shadow: 0 0 7px #21e6ff; }
.communication-panel__close { width: 30px; height: 30px; padding: 0; border: 1px solid rgba(98, 216, 255, .45); border-radius: 50%; background: rgba(2, 21, 44, .72); color: #ccefff; font-size: 21px; line-height: 1; cursor: pointer; }
.communication-panel__close:hover,
.communication-panel__close:focus-visible { border-color: #62d8ff; box-shadow: 0 0 10px rgba(33, 230, 255, .45); outline: none; }
.communication-panel__table { height: 390px; }
.communication-panel__table :deep(.el-table) { --el-table-border-color: transparent; --el-table-border: 0; --el-table-bg-color: transparent; --el-table-tr-bg-color: transparent; --el-fill-color-lighter: rgba(98, 148, 211, .12); --el-table-row-hover-bg-color: rgba(33, 230, 255, .08); background: transparent; color: #f5fbff; font-size: 16px; }
.communication-panel__table :deep(.el-table::before),
.communication-panel__table :deep(.el-table__inner-wrapper::before) { display: none; }
.communication-panel__table :deep(th.el-table__cell) { height: 62px; padding: 0; border: 0; background: linear-gradient(180deg, rgba(55, 99, 165, .98), rgba(46, 86, 149, .98)); color: #fff; font-size: 22px; font-weight: 800; }
.communication-panel__table :deep(td.el-table__cell) { height: 54px; padding: 0; border-bottom: 1px solid rgba(171, 214, 255, .06); background: transparent; }
.communication-panel__table :deep(.el-table__body tr.el-table__row--striped td.el-table__cell) { background: rgba(108, 156, 217, .08); }
.communication-panel__table :deep(.el-scrollbar__bar) { display: none; }
.communication-panel__table :deep(.el-table__empty-text) { color: #8fb1c8; }
.communication-panel__time { font-variant-numeric: tabular-nums; font-weight: 700; }
.communication-panel__flow { display: inline-flex; align-items: center; justify-content: center; gap: 18px; }
.communication-panel__flow b { font-size: 18px; }
.communication-panel__flow i { width: 0; height: 0; border-top: 8px solid transparent; border-bottom: 8px solid transparent; border-left: 14px solid #ffe36d; filter: drop-shadow(0 0 3px rgba(255, 227, 109, .3)); }
.communication-panel__message { display: block; max-width: 100%; overflow: hidden; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.communication-panel__footer { height: 86px; display: flex; align-items: flex-end; justify-content: center; gap: 12px; padding-bottom: 2px; color: #39bfdc; font-size: 12px; }
.communication-panel__footer em { padding: 3px 5px; background: #07304f; color: #d9f5ff; font-style: normal; }
.communication-panel__footer :deep(.el-pagination) { --el-pagination-bg-color: #052c4c; --el-pagination-button-color: #eefaff; --el-pagination-hover-color: #21e6ff; --el-pagination-button-disabled-bg-color: #052c4c; gap: 4px; }
.communication-panel__footer :deep(.el-pager li),
.communication-panel__footer :deep(.btn-prev),
.communication-panel__footer :deep(.btn-next) { min-width: 20px; width: 20px; height: 20px; margin: 0 !important; border-radius: 0; background: #052c4c !important; color: #eaf8ff; }
.communication-panel__footer :deep(.el-pager li.is-active) { background: #16bfe8 !important; color: #fff; }

@media (max-width: 1080px), (max-height: 720px) {
  .communication-panel { width: min(900px, calc(100vw - 24px)); height: min(540px, calc(100vh - 48px)); min-height: 0; transform: scale(.9); }
}
</style>
