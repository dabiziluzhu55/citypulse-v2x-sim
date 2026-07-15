<script setup lang="ts">
import { computed } from 'vue'
import OverviewStatCard from './OverviewStatCard.vue'
import type { OverviewUpdateSource } from '../../composables/useRunOverview'
import type { RunOverview } from '../../types/overview'
import {
  formatAlgorithm,
  formatNumber,
  formatSimTime,
  formatStatus,
} from '../../utils/format'

const props = defineProps<{
  overview: RunOverview | null
  loading: boolean
  error: string | null
  wsConnected: boolean
  lastSource: OverviewUpdateSource | null
  embedded?: boolean
}>()

const statusType = computed(() => {
  switch (props.overview?.status) {
    case 'running':
      return 'success'
    case 'paused':
      return 'warning'
    case 'error':
      return 'danger'
    case 'stopped':
      return 'info'
    default:
      return 'info'
  }
})

const sourceLabel = computed(() => {
  if (props.lastSource === 'ws') {
    return 'WebSocket 实时'
  }
  if (props.lastSource === 'poll') {
    return 'HTTP 轮询'
  }
  return '--'
})
</script>

<template>
  <section :class="embedded ? 'overview-panel embedded' : 'overview-panel'">
    <header v-if="!embedded" class="panel-header">
      <div class="title-block">
        <p class="panel-kicker">系统首页 · 仿真总览</p>
        <h2 class="panel-title">
          {{ overview?.scenario_name ?? '等待加载仿真场景…' }}
        </h2>
        <p v-if="overview" class="panel-meta">
          场景 ID：{{ overview.scenario_id }} · Run ID：{{ overview.run_id }}
        </p>
      </div>

      <div class="status-block">
        <el-tag
          v-if="overview"
          :type="statusType"
          effect="dark"
          class="status-tag"
        >
          {{ formatStatus(overview.status) }}
        </el-tag>
        <el-tag
          :type="wsConnected ? 'success' : 'warning'"
          effect="dark"
          class="status-tag"
        >
          {{ wsConnected ? 'WS 已连接' : 'WS 未连接' }}
        </el-tag>
        <span class="source-label">最近更新：{{ sourceLabel }}</span>
      </div>
    </header>

    <div v-if="loading" class="panel-state">
      <el-skeleton animated :rows="4" />
    </div>

    <div v-else-if="error && !overview" class="panel-state">
      <el-alert :title="error" type="error" show-icon :closable="false" />
    </div>

    <div v-else-if="overview" class="stats-grid">
      <OverviewStatCard
        label="仿真时间"
        :value="formatSimTime(overview.sim_time)"
        accent="cyan"
      />
      <OverviewStatCard
        label="车辆总数"
        :value="String(overview.vehicle_count)"
        unit="辆"
      />
      <OverviewStatCard
        label="活跃车辆"
        :value="String(overview.active_vehicle_count)"
        unit="辆"
        accent="success"
      />
      <OverviewStatCard
        label="拥堵路口数"
        :value="String(overview.congested_intersections)"
        unit="个"
        accent="warning"
      />
      <OverviewStatCard
        label="平均速度"
        :value="formatNumber(overview.avg_speed)"
        unit="m/s"
      />
      <OverviewStatCard
        label="平均等待时间"
        :value="formatNumber(overview.avg_waiting_time)"
        unit="s"
      />
      <OverviewStatCard
        label="平均排队长度"
        :value="formatNumber(overview.avg_queue_length)"
        unit="veh"
      />
      <OverviewStatCard
        label="当前管控算法"
        :value="formatAlgorithm(overview.algorithm)"
        accent="cyan"
      />
      <OverviewStatCard
        label="云边端协同"
        :value="overview.cloud_edge_enabled ? '已启用' : '未启用'"
        :accent="overview.cloud_edge_enabled ? 'success' : 'danger'"
      />
    </div>

    <p v-if="error && overview" class="inline-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.overview-panel {
  padding: 20px 24px;
  border: 1px solid var(--cp-border);
  border-radius: 14px;
  background:
    radial-gradient(circle at top right, rgba(0, 210, 255, 0.12), transparent 42%),
    var(--cp-bg-panel);
  backdrop-filter: blur(8px);
  box-shadow: var(--cp-glow);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.title-block {
  min-width: 0;
}

.panel-kicker {
  margin: 0 0 6px;
  color: var(--cp-accent-soft);
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  text-shadow: var(--cp-glow);
}

.panel-title {
  margin: 0;
  color: var(--cp-text-primary);
  font-size: 24px;
  font-weight: 700;
  line-height: 1.3;
  text-shadow: var(--cp-glow-strong);
}

.panel-meta {
  margin: 8px 0 0;
  color: var(--cp-text-muted);
  font-size: 13px;
}

.status-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.status-tag {
  border: 1px solid rgba(0, 255, 255, 0.35);
}

.source-label {
  color: var(--cp-text-muted);
  font-size: 12px;
}

.panel-state {
  padding: 8px 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.inline-error {
  margin: 14px 0 0;
  color: var(--cp-danger);
  font-size: 13px;
}

.overview-panel.embedded {
  border: 0;
  box-shadow: none;
  background: transparent;
  padding: 0;
  backdrop-filter: none;
}

:deep(.el-skeleton__item) {
  background: rgba(0, 210, 255, 0.08);
}

:deep(.el-alert) {
  background: rgba(255, 95, 122, 0.12);
  border: 1px solid rgba(255, 95, 122, 0.35);
}

@media (max-width: 1200px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .panel-header {
    flex-direction: column;
  }

  .status-block {
    align-items: flex-start;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
