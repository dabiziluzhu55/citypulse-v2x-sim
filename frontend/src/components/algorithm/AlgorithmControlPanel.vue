<script setup lang="ts">
import { formatNumber } from '../../utils/format'
import { formatActionType } from '../../constants/collaborationOptions'
import { formatAlgorithmType } from '../../constants/algorithmOptions'
import type { AlgorithmComparisonRow, AlgorithmDefinition } from '../../types/algorithm'

defineProps<{
  runId: string
  algorithms: AlgorithmDefinition[]
  loadingList: boolean
  switching: boolean
  listError: string | null
  switchError: string | null
  switchMessage: string | null
  selectedAlgorithmId: string
  currentAlgorithm: AlgorithmDefinition | null
  parameters: { min_green: number; max_green: number }
  inputState: {
    min_green: number
    max_green: number
    cloud_edge_enabled: boolean
    congested_intersections: number | null
    active_vehicle_count: number | null
  }
  outputActions: Array<{
    intersection_id: string
    action_type: string
    target_phase: number
    duration: number
    status: string
  }>
  comparisonRows: AlgorithmComparisonRow[]
}>()

const emit = defineEmits<{
  'update:selectedAlgorithmId': [value: string]
  'update:parameters': [value: { min_green: number; max_green: number }]
  apply: []
}>()

function patchParameters(patch: Partial<{ min_green: number; max_green: number }>, current: {
  min_green: number
  max_green: number
}) {
  emit('update:parameters', { ...current, ...patch })
}
</script>

<template>
  <section class="algorithm-panel">
    <el-alert
      v-if="listError"
      :title="listError"
      type="error"
      show-icon
      :closable="false"
      class="inline-alert"
    />

    <div class="current-block">
      <span class="label">当前算法</span>
      <strong class="current-name">
        {{ currentAlgorithm?.name ?? '未选择' }}
      </strong>
      <el-tag v-if="currentAlgorithm" effect="dark" size="small">
        {{ formatAlgorithmType(currentAlgorithm.type) }}
      </el-tag>
    </div>

    <div class="field-block">
      <label class="field-label">可选算法</label>
      <el-select
        :model-value="selectedAlgorithmId"
        :loading="loadingList"
        :disabled="!runId"
        placeholder="选择算法"
        @update:model-value="emit('update:selectedAlgorithmId', String($event))"
      >
        <el-option
          v-for="algorithm in algorithms"
          :key="algorithm.algorithm_id"
          :label="algorithm.name"
          :value="algorithm.algorithm_id"
        >
          <div class="option-row">
            <span>{{ algorithm.name }}</span>
            <span class="option-type">{{ formatAlgorithmType(algorithm.type) }}</span>
          </div>
        </el-option>
      </el-select>
      <p v-if="currentAlgorithm" class="field-desc">{{ currentAlgorithm.description }}</p>
    </div>

    <div class="field-block">
      <label class="field-label">算法输入状态</label>
      <div class="state-grid">
        <div class="state-item">
          <span>最小绿灯</span>
          <el-input-number
            :model-value="parameters.min_green"
            :min="5"
            :max="30"
            @update:model-value="patchParameters({ min_green: Number($event) }, parameters)"
          />
        </div>
        <div class="state-item">
          <span>最大绿灯</span>
          <el-input-number
            :model-value="parameters.max_green"
            :min="30"
            :max="90"
            @update:model-value="patchParameters({ max_green: Number($event) }, parameters)"
          />
        </div>
        <div class="state-item">
          <span>云边端协同</span>
          <strong>{{ inputState.cloud_edge_enabled ? '已启用' : '未启用' }}</strong>
        </div>
        <div class="state-item">
          <span>活跃车辆</span>
          <strong>{{ inputState.active_vehicle_count ?? '--' }}</strong>
        </div>
        <div class="state-item">
          <span>拥堵路口</span>
          <strong>{{ inputState.congested_intersections ?? '--' }}</strong>
        </div>
      </div>
    </div>

    <div class="field-block">
      <label class="field-label">算法输出动作</label>
      <div class="action-list">
        <article v-for="action in outputActions" :key="action.intersection_id" class="action-item">
          <strong>{{ action.intersection_id }}</strong>
          <span>
            {{ formatActionType(action.action_type) }} · 相位 {{ action.target_phase }} ·
            {{ action.duration }}s
          </span>
          <el-tag size="small" effect="plain">{{ action.status }}</el-tag>
        </article>
        <p v-if="outputActions.length === 0" class="empty-tip">暂无路端输出动作</p>
      </div>
    </div>

    <div class="button-row">
      <el-button
        type="primary"
        :loading="switching"
        :disabled="!runId || !selectedAlgorithmId"
        @click="emit('apply')"
      >
        切换算法
      </el-button>
    </div>

    <el-alert
      v-if="switchError"
      :title="switchError"
      type="error"
      show-icon
      :closable="false"
      class="inline-alert"
    />
    <p v-if="switchMessage" class="success-message">{{ switchMessage }}</p>

    <div class="field-block">
      <label class="field-label">与固定配时对比</label>
      <table class="compare-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>当前算法</th>
            <th>固定配时基线</th>
            <th>变化</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in comparisonRows" :key="row.key">
            <td>{{ row.label }}</td>
            <td>
              {{ row.current != null ? `${formatNumber(row.current)} ${row.unit}` : '--' }}
            </td>
            <td>
              {{ row.baseline != null ? `${formatNumber(row.baseline)} ${row.unit}` : '--' }}
            </td>
            <td :class="row.improved === true ? 'good' : row.improved === false ? 'bad' : ''">
              {{
                row.delta != null
                  ? `${row.delta > 0 ? '+' : ''}${row.delta.toFixed(1)}%`
                  : '--'
              }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="compare-note">基线指标在运行固定配时算法时自动采样更新。</p>
    </div>
  </section>
</template>

<style scoped>
.algorithm-panel {
  display: grid;
  gap: 12px;
}

.inline-alert {
  margin-bottom: 4px;
}

.current-block {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border: 1px solid rgba(33, 230, 255, 0.18);
  border-radius: 8px;
  background: rgba(2, 16, 31, 0.55);
}

.label {
  color: #78aeca;
  font-size: 12px;
}

.current-name {
  color: #21e6ff;
  font-size: 16px;
}

.field-block {
  display: grid;
  gap: 8px;
}

.field-label {
  color: #78aeca;
  font-size: 12px;
}

.field-desc {
  margin: 0;
  color: #78aeca;
  font-size: 12px;
  line-height: 1.5;
}

.option-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.option-type {
  color: #78aeca;
  font-size: 12px;
}

.state-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.state-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid rgba(33, 230, 255, 0.12);
  border-radius: 6px;
  background: rgba(2, 16, 31, 0.38);
  color: #78aeca;
  font-size: 12px;
}

.state-item strong {
  color: #f2fbff;
}

.action-list {
  display: grid;
  gap: 8px;
}

.action-item {
  display: grid;
  grid-template-columns: 64px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 8px;
  border: 1px solid rgba(33, 230, 255, 0.12);
  border-radius: 6px;
  background: rgba(2, 16, 31, 0.38);
  color: #78aeca;
  font-size: 12px;
}

.action-item strong {
  color: #21e6ff;
}

.empty-tip,
.compare-note,
.success-message {
  margin: 0;
  color: #78aeca;
  font-size: 12px;
}

.success-message {
  color: #20f6a4;
}

.button-row {
  display: flex;
  justify-content: flex-end;
}

.compare-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.compare-table th,
.compare-table td {
  padding: 7px 6px;
  border-bottom: 1px solid rgba(33, 230, 255, 0.14);
  text-align: left;
}

.compare-table th {
  color: #21e6ff;
  background: rgba(33, 230, 255, 0.08);
}

.compare-table td {
  color: #78aeca;
}

.compare-table td.good {
  color: #20f6a4;
}

.compare-table td.bad {
  color: #ff4d6d;
}
</style>
