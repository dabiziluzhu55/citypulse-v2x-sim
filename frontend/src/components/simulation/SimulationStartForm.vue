<script setup lang="ts">
import { ref, watch } from 'vue'
import { ALGORITHM_OPTIONS } from '../../constants/simulationOptions'
import type { StartRunRequest } from '../../types/simulation'

const props = defineProps<{
  initialScenarioId?: string
  loading: boolean
  error: string | null
  embedded?: boolean
}>()

const emit = defineEmits<{
  start: [payload: StartRunRequest]
}>()

const form = ref<StartRunRequest>({
  scenario_id: props.initialScenarioId ?? '',
  algorithm: 'fixed_time',
  cloud_edge_enabled: true,
  realtime: true,
  step_length: 1.0,
})

watch(
  () => props.initialScenarioId,
  (scenarioId) => {
    if (scenarioId) {
      form.value.scenario_id = scenarioId
    }
  },
)

function submit() {
  if (!form.value.scenario_id.trim()) {
    return
  }
  emit('start', {
    ...form.value,
    scenario_id: form.value.scenario_id.trim(),
  })
}
</script>

<template>
  <section :class="embedded ? 'embedded-form' : 'panel'">
    <header v-if="!embedded" class="panel-header">
      <div>
        <p class="panel-kicker">3.1 启动仿真</p>
        <h3 class="panel-title">仿真启动配置</h3>
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

    <div class="form-grid">
      <label class="form-item wide">
        <span>Scenario ID</span>
        <el-input
          v-model="form.scenario_id"
          placeholder="例如：scenario_20260704_001"
        />
      </label>

      <label class="form-item">
        <span>管控算法</span>
        <el-select v-model="form.algorithm">
          <el-option
            v-for="option in ALGORITHM_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
      </label>

      <label class="form-item">
        <span>步长 (s)</span>
        <el-input-number
          v-model="form.step_length"
          :min="0.05"
          :max="5"
          :step="0.05"
        />
      </label>

      <label class="form-item switch-item">
        <span>云边端协同</span>
        <el-switch v-model="form.cloud_edge_enabled" />
      </label>

      <label class="form-item switch-item">
        <span>实时模式</span>
        <el-switch v-model="form.realtime" />
      </label>
    </div>

    <div class="actions">
      <el-button
        type="primary"
        :loading="loading"
        :disabled="!form.scenario_id.trim()"
        @click="submit"
      >
        启动仿真
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

.inline-alert {
  margin-bottom: 14px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--cp-text-secondary);
  font-size: 13px;
}

.form-item.wide {
  grid-column: 1 / -1;
}

.switch-item {
  justify-content: space-between;
  flex-direction: row;
  align-items: center;
}

.actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 900px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
.embedded-form {
  padding: 0;
}
</style>
