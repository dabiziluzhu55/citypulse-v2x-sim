<script setup lang="ts">
import { formatNumber, formatSimTime } from '../../utils/format'
import {
  formatEventLevel,
  formatEventType,
  formatLocation,
  formatSuggestion,
  inferEventCause,
  EVENT_LEVEL_CLASS,
} from '../../constants/eventOptions'
import type { PredictionResponse, TrafficEvent } from '../../types/events'

defineProps<{
  runId: string
  events: TrafficEvent[]
  prediction: PredictionResponse | null
  predictionTarget: string
  predictionHorizon: number
  targetOptions: string[]
  eventsLoading: boolean
  predictionLoading: boolean
  eventsError: string | null
  predictionError: string | null
  wsConnected: boolean
}>()

const emit = defineEmits<{
  'update:predictionTarget': [value: string]
  refresh: []
}>()
</script>

<template>
  <section class="events-panel">
    <div v-if="!runId" class="empty-state">
      <p>启动仿真后展示事件识别与交通流预测结果。</p>
    </div>

    <template v-else>
      <div class="panel-head">
        <span class="head-title">事件识别</span>
        <span class="head-meta">{{ wsConnected ? 'WS + 30s 刷新' : '30s 刷新' }}</span>
      </div>

      <el-alert
        v-if="eventsError"
        :title="eventsError"
        type="error"
        show-icon
        :closable="false"
        class="inline-alert"
      />

      <div v-if="eventsLoading && events.length === 0" class="empty-state">
        <el-skeleton animated :rows="3" />
      </div>

      <div v-else class="event-list">
        <article v-for="event in events" :key="event.event_id" class="event-card">
          <div class="event-head">
            <div>
              <h4 class="event-title">
                {{ formatEventType(event.type) }} · {{ formatLocation(event.location) }}
              </h4>
              <p class="event-time">t = {{ formatSimTime(event.time) }}</p>
            </div>
            <span class="pill" :class="EVENT_LEVEL_CLASS[event.level]">
              {{ formatEventLevel(event.level) }}
            </span>
          </div>

          <p class="event-desc">{{ event.description }}</p>

          <div class="event-meta">
            <span>事件原因：{{ inferEventCause(event.evidence) }}</span>
            <span>推荐动作：{{ formatSuggestion(event.suggestion) }}</span>
          </div>

          <div v-if="event.evidence" class="evidence-grid">
            <span v-if="event.evidence.avg_speed != null">
              速度 {{ formatNumber(event.evidence.avg_speed) }} m/s
            </span>
            <span v-if="event.evidence.queue_length != null">
              排队 {{ event.evidence.queue_length }} 辆
            </span>
            <span v-if="event.evidence.avg_waiting_time != null">
              等待 {{ formatNumber(event.evidence.avg_waiting_time) }} s
            </span>
          </div>
        </article>

        <p v-if="events.length === 0" class="empty-state">暂无识别事件</p>
      </div>

      <div class="prediction-section">
        <div class="panel-head">
          <span class="head-title">交通流预测</span>
          <el-button size="small" text @click="emit('refresh')">刷新</el-button>
        </div>

        <div class="prediction-controls">
          <label class="control-item">
            <span>预测目标</span>
            <el-select
              :model-value="predictionTarget"
              @update:model-value="emit('update:predictionTarget', String($event))"
            >
              <el-option v-for="target in targetOptions" :key="target" :label="target" :value="target" />
            </el-select>
          </label>
          <label class="control-item">
            <span>预测窗口</span>
            <strong>{{ predictionHorizon }} s</strong>
          </label>
        </div>

        <el-alert
          v-if="predictionError"
          :title="predictionError"
          type="warning"
          show-icon
          :closable="false"
          class="inline-alert"
        />

        <div v-if="predictionLoading && !prediction" class="empty-state">
          <el-skeleton animated :rows="2" />
        </div>

        <template v-else-if="prediction">
          <p class="prediction-meta">
            模型 {{ prediction.model }} · 更新于 t={{ formatSimTime(prediction.updated_at) }}
          </p>

          <table class="prediction-table">
            <thead>
              <tr>
                <th>时间偏移</th>
                <th>预测流量</th>
                <th>预测排队</th>
                <th>拥堵风险</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="point in prediction.predictions" :key="point.time_offset">
                <td>+{{ point.time_offset }}s</td>
                <td>{{ point.predicted_flow }} veh/period</td>
                <td>{{ point.predicted_queue }} veh</td>
                <td>
                  <div class="risk-cell">
                    <div class="risk-track">
                      <div
                        class="risk-fill"
                        :style="{ width: `${Math.round(point.congestion_risk * 100)}%` }"
                      />
                    </div>
                    <span>{{ (point.congestion_risk * 100).toFixed(0) }}%</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>
    </template>
  </section>
</template>

<style scoped>
.events-panel {
  display: grid;
  gap: 12px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.head-title {
  color: #21e6ff;
  font-size: 13px;
  font-weight: 600;
}

.head-meta {
  color: #78aeca;
  font-size: 12px;
}

.inline-alert {
  margin-bottom: 4px;
}

.empty-state {
  color: #78aeca;
  font-size: 13px;
}

.event-list {
  display: grid;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
}

.event-card {
  border: 1px solid rgba(33, 230, 255, 0.16);
  background: rgba(2, 16, 31, 0.56);
  border-radius: 7px;
  padding: 9px 10px;
}

.event-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.event-title {
  margin: 0;
  color: #f4fcff;
  font-size: 14px;
  font-weight: 600;
}

.event-time {
  margin: 4px 0 0;
  color: #78aeca;
  font-size: 11px;
}

.event-desc {
  margin: 0 0 8px;
  color: #78aeca;
  font-size: 12px;
  line-height: 1.5;
}

.event-meta {
  display: grid;
  gap: 4px;
  margin-bottom: 8px;
  color: #78aeca;
  font-size: 11px;
}

.evidence-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: #21e6ff;
  font-size: 11px;
}

.pill {
  border-radius: 99px;
  padding: 3px 7px;
  font-size: 12px;
  border: 1px solid currentColor;
  background: rgba(255, 255, 255, 0.06);
  white-space: nowrap;
}

.pill.red {
  color: #ff4d6d;
}

.pill.yellow {
  color: #ffd05a;
}

.pill.cyan {
  color: #21e6ff;
}

.prediction-section {
  display: grid;
  gap: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(33, 230, 255, 0.12);
}

.prediction-controls {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #78aeca;
  font-size: 12px;
}

.control-item strong {
  color: #f2fbff;
}

.prediction-meta {
  margin: 0;
  color: #78aeca;
  font-size: 12px;
}

.prediction-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.prediction-table th,
.prediction-table td {
  padding: 7px 6px;
  border-bottom: 1px solid rgba(33, 230, 255, 0.14);
  text-align: left;
}

.prediction-table th {
  color: #21e6ff;
  background: rgba(33, 230, 255, 0.08);
}

.prediction-table td {
  color: #78aeca;
}

.risk-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.risk-track {
  flex: 1;
  height: 8px;
  border-radius: 99px;
  background: rgba(33, 230, 255, 0.12);
  overflow: hidden;
}

.risk-fill {
  height: 100%;
  background: linear-gradient(90deg, #ffd05a, #ff4d6d);
}
</style>
