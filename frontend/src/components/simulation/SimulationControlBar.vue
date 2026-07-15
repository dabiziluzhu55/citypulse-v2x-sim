<script setup lang="ts">
import { computed } from 'vue'
import type { ControlCommand, RunLifecycleStatus } from '../../types/simulation'

const props = defineProps<{
  runId: string
  status: RunLifecycleStatus | null
  loading: boolean
  error: string | null
  message: string | null
  embedded?: boolean
}>()

const emit = defineEmits<{
  control: [command: ControlCommand]
}>()

const canPause = computed(() => props.status === 'running')
const canResume = computed(() => props.status === 'paused')
const canStop = computed(
  () => props.status === 'running' || props.status === 'paused' || props.status === 'starting',
)
const canReset = computed(() => props.status === 'stopped' || props.status === 'paused')
const canStep = computed(() => props.status === 'paused')

function isDisabled(command: ControlCommand) {
  switch (command) {
    case 'pause':
      return !canPause.value
    case 'resume':
      return !canResume.value
    case 'stop':
      return !canStop.value
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
        <p class="panel-kicker">3.2 控制仿真</p>
        <h3 class="panel-title">仿真控制</h3>
        <p class="panel-meta">当前 Run ID：{{ runId || '未启动' }}</p>
      </div>
    </header>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="inline-alert"
    />

    <p v-if="message" class="message">{{ message }}</p>

    <div class="control-row">
      <el-button
        type="primary"
        plain
        :loading="loading"
        :disabled="!runId || isDisabled('pause')"
        @click="emit('control', 'pause')"
      >
        暂停
      </el-button>
      <el-button
        type="primary"
        plain
        :loading="loading"
        :disabled="!runId || isDisabled('resume')"
        @click="emit('control', 'resume')"
      >
        继续
      </el-button>
      <el-button
        type="warning"
        plain
        :loading="loading"
        :disabled="!runId || isDisabled('stop')"
        @click="emit('control', 'stop')"
      >
        停止
      </el-button>
      <el-button
        plain
        :loading="loading"
        :disabled="!runId || isDisabled('reset')"
        @click="emit('control', 'reset')"
      >
        重置
      </el-button>
      <el-button
        plain
        :loading="loading"
        :disabled="!runId || isDisabled('step')"
        @click="emit('control', 'step')"
      >
        单步
      </el-button>
    </div>
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
  margin-bottom: 14px;
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

.panel-meta {
  margin: 8px 0 0;
  color: var(--cp-text-muted);
  font-size: 13px;
}

.inline-alert {
  margin-bottom: 12px;
}

.message {
  margin: 0 0 12px;
  color: var(--cp-text-secondary);
  font-size: 13px;
}

.control-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.embedded-form {
  padding: 0;
}
</style>
