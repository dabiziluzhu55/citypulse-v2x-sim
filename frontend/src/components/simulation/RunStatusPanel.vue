<script setup lang="ts">
import { computed } from 'vue'
import OverviewStatCard from '../overview/OverviewStatCard.vue'
import type { ControlCommand, RunLifecycleStatus, RunStatus } from '../../types/simulation'
import { formatRunLifecycleStatus, formatSimTime } from '../../utils/format'

const props = defineProps<{
  status: RunStatus | null
  loading: boolean
  error: string | null
  runId: string
  controlStatus: RunLifecycleStatus | null
  controlling: boolean
  embedded?: boolean
}>()

const emit = defineEmits<{
  control: [command: ControlCommand]
}>()

const statusType = computed(() => {
  switch (props.status?.status) {
    case 'running':
      return 'success'
    case 'starting':
      return 'warning'
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

const canPause = computed(() => props.controlStatus === 'running')
const canResume = computed(() => props.controlStatus === 'paused')
const canReset = computed(() => props.controlStatus === 'stopped' || props.controlStatus === 'paused')
const canStep = computed(() => props.controlStatus === 'paused')

function isDisabled(command: ControlCommand) {
  switch (command) {
    case 'pause':
      return !canPause.value
    case 'resume':
      return !canResume.value
    case 'reset':
      return !canReset.value
    case 'step':
      return !canStep.value
    default:
      return true
  }
}
</script>

<template>
  <section :class="embedded ? 'embedded-form' : 'panel'">
    <header v-if="!embedded" class="panel-header">
      <div>
        <p class="panel-kicker">3.3 仿真状态</p>
        <h3 class="panel-title">运行状态</h3>
      </div>
      <el-tag v-if="status" :type="statusType" effect="dark">
        {{ formatRunLifecycleStatus(status.status) }}
      </el-tag>
    </header>

    <div v-if="!runId" class="empty-state">
      <p>尚未启动仿真，请配置场景并点击「启动仿真」。</p>
    </div>

    <div v-else-if="loading && !status" class="empty-state">
      <el-skeleton animated :rows="2" />
    </div>

    <el-alert
      v-else-if="error && !status"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    />

    <div v-else-if="status" class="stats-grid">
      <OverviewStatCard label="Run ID" :value="status.run_id" accent="cyan" />
      <OverviewStatCard
        label="仿真时间"
        :value="formatSimTime(status.sim_time)"
        accent="cyan"
      />
      <OverviewStatCard label="步数" :value="String(status.step)" />
      <OverviewStatCard label="车辆数" :value="String(status.vehicle_count)" unit="辆" />
    </div>

    <div v-if="runId" class="advanced-controls">
      <span class="controls-label">高级控制</span>
      <div class="control-row">
        <el-button
          size="small"
          plain
          :loading="controlling"
          :disabled="!runId || isDisabled('pause')"
          @click="emit('control', 'pause')"
        >
          暂停
        </el-button>
        <el-button
          size="small"
          plain
          :loading="controlling"
          :disabled="!runId || isDisabled('resume')"
          @click="emit('control', 'resume')"
        >
          继续
        </el-button>
        <el-button
          size="small"
          plain
          :loading="controlling"
          :disabled="!runId || isDisabled('reset')"
          @click="emit('control', 'reset')"
        >
          重置
        </el-button>
        <el-button
          size="small"
          plain
          :loading="controlling"
          :disabled="!runId || isDisabled('step')"
          @click="emit('control', 'step')"
        >
          单步
        </el-button>
      </div>
    </div>

    <p v-if="status?.message" class="status-message">{{ status.message }}</p>
  </section>
</template>

<style scoped>
.panel {
  padding: 20px;
  border: 1px solid var(--cp-border);
  border-radius: 12px;
  background: var(--cp-bg-panel);
  backdrop-filter: blur(8px);
  box-shadow: var(--cp-glow);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 16px;
}

.panel-kicker {
  margin: 0 0 6px;
  color: var(--cp-accent-soft);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.panel-title {
  margin: 0;
  color: var(--cp-text-primary);
  font-size: 20px;
  text-shadow: var(--cp-glow);
}

.empty-state {
  color: var(--cp-text-muted);
  font-size: 13px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.advanced-controls {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(33, 230, 255, 0.12);
}

.controls-label {
  color: #78aeca;
  font-size: 12px;
}

.control-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.status-message {
  margin: 14px 0 0;
  color: var(--cp-text-secondary);
  font-size: 13px;
}

@media (max-width: 900px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}

.embedded-form {
  padding: 0;
}
</style>
