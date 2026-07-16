<script setup lang="ts">
import { computed } from 'vue'
import {
  formatActionType,
  formatAdviceType,
  formatAlgorithm,
  formatPhaseLabel,
  formatStrategy,
} from '../../constants/collaborationOptions'
import type {
  CollaborationLogEntry,
  CollaborationStateSnapshot,
} from '../../types/collaboration'

const props = defineProps<{
  state: CollaborationStateSnapshot | null
  logEntries: CollaborationLogEntry[]
  loading: boolean
  error: string | null
  wsConnected: boolean
  runId: string
  vehicleOnlineCount: number
  cloudStrategyLabel: string
}>()

const isCongested = computed(() => {
  const reason = props.state?.cloud.reason?.toLowerCase() ?? ''
  return reason.includes('queue') || reason.includes('拥堵') || reason.includes('high')
})

const issuedCommand = computed(() => {
  const edge = props.state?.edges[0]
  if (!edge) {
    return '--'
  }
  return `给 ${edge.intersection_id} ${formatActionType(edge.last_action.action_type)} ${edge.last_action.duration}s`
})

function formatAdvice(vehicle: CollaborationStateSnapshot['vehicles'][number]) {
  const advice = vehicle.received_advice
  if (advice.recommended_speed != null) {
    return `${formatAdviceType(advice.type)} ${advice.recommended_speed} m/s`
  }
  if (advice.recommended_path) {
    return `${formatAdviceType(advice.type)} ${advice.recommended_path}`
  }
  return formatAdviceType(advice.type)
}
</script>

<template>
  <section class="collaboration-panel">
    <div v-if="!runId" class="empty-state">
      <p>启动仿真后展示车路云协同状态。</p>
    </div>

    <div v-else-if="loading && !state" class="empty-state">
      <el-skeleton animated :rows="4" />
    </div>

    <el-alert
      v-else-if="error && !state"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    />

    <template v-else-if="state">
      <div class="flow">
        <div class="node">
          <h3>车端</h3>
          <p>车辆状态上报</p>
          <p><span class="cyan">{{ vehicleOnlineCount }}</span> 辆在线</p>
        </div>
        <div class="arrow">→</div>
        <div class="node">
          <h3>路端</h3>
          <p>路口 Agent 执行</p>
          <p><span class="cyan">{{ state.edges.length }}</span> 个路端节点</p>
        </div>
        <div class="arrow">→</div>
        <div class="node">
          <h3>云端</h3>
          <p>区域策略调度</p>
          <p><span class="yellow">{{ cloudStrategyLabel }}</span></p>
        </div>
      </div>

      <div class="section-grid">
        <article class="section-card">
          <h4>5.1 车端区域</h4>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>车辆 ID</th>
                  <th>位置</th>
                  <th>速度</th>
                  <th>等待</th>
                  <th>接收指令</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="vehicle in state.vehicles" :key="vehicle.vehicle_id">
                  <td>{{ vehicle.vehicle_id }}</td>
                  <td>{{ vehicle.lane_id }}</td>
                  <td>{{ vehicle.speed.toFixed(1) }} m/s</td>
                  <td>{{ vehicle.waiting_time?.toFixed(1) ?? '--' }} s</td>
                  <td>{{ formatAdvice(vehicle) }}</td>
                </tr>
                <tr v-if="state.vehicles.length === 0">
                  <td colspan="5">暂无车端数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="section-card">
          <h4>5.2 路端区域</h4>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>路口 ID</th>
                  <th>当前相位</th>
                  <th>排队长度</th>
                  <th>冲突检测</th>
                  <th>执行动作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="edge in state.edges" :key="edge.edge_agent_id">
                  <td>{{ edge.intersection_id }}</td>
                  <td>{{ formatPhaseLabel(edge.local_state.current_phase) }}</td>
                  <td>{{ edge.local_state.queue_length }} 辆</td>
                  <td>
                    {{
                      edge.local_rule_check.conflict_free ? '允许切换相位' : '存在冲突'
                    }}
                  </td>
                  <td>
                    {{ formatActionType(edge.last_action.action_type) }}
                    {{ edge.last_action.duration }}s
                  </td>
                </tr>
                <tr v-if="state.edges.length === 0">
                  <td colspan="5">暂无路端数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="section-card">
          <h4>5.3 云端区域</h4>
          <div class="cloud-grid">
            <div class="cloud-item">
              <span>区域拥堵判断</span>
              <strong :class="isCongested ? 'red' : 'green'">
                {{ isCongested ? '拥堵' : '正常' }}
              </strong>
            </div>
            <div class="cloud-item">
              <span>全局策略</span>
              <strong class="cyan">{{ formatStrategy(state.cloud.strategy) }}</strong>
            </div>
            <div class="cloud-item">
              <span>下发命令</span>
              <strong>{{ issuedCommand }}</strong>
            </div>
            <div class="cloud-item">
              <span>算法类型</span>
              <strong>{{ formatAlgorithm(state.cloud.algorithm) }}</strong>
            </div>
            <div class="cloud-item wide">
              <span>目标区域</span>
              <strong>{{ state.cloud.target_area || '--' }}</strong>
            </div>
            <div class="cloud-item wide">
              <span>决策原因</span>
              <strong>{{ state.cloud.reason || '--' }}</strong>
            </div>
          </div>
        </article>
      </div>

      <div class="message-log">
        <div class="log-head">
          <span>协同消息流</span>
          <span>{{ wsConnected ? 'WS 已连接' : 'WS 未连接' }}</span>
        </div>
        <div v-for="entry in logEntries" :key="entry.id" class="log-line">
          <span>{{ entry.timeLabel }}</span>
          <span><strong>{{ entry.source }}</strong> {{ entry.message }}</span>
        </div>
        <p v-if="logEntries.length === 0" class="log-empty">暂无协同消息</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.collaboration-panel {
  display: grid;
  gap: 12px;
}

.empty-state {
  color: #78aeca;
  font-size: 13px;
}

.flow {
  display: grid;
  grid-template-columns: 1fr 34px 1fr 34px 1fr;
  align-items: stretch;
  gap: 8px;
}

.node {
  border: 1px solid rgba(33, 230, 255, 0.22);
  background: rgba(2, 16, 31, 0.58);
  border-radius: 8px;
  padding: 9px;
}

.node h3 {
  margin: 0 0 8px;
  color: #f2fbff;
  font-size: 14px;
}

.node p {
  margin: 3px 0;
  color: #78aeca;
  font-size: 12px;
}

.arrow {
  display: grid;
  place-items: center;
  color: #21e6ff;
  font-size: 22px;
  text-shadow: 0 0 10px rgba(33, 230, 255, 0.7);
}

.cyan {
  color: #21e6ff;
}

.yellow {
  color: #ffd05a;
}

.green {
  color: #20f6a4;
}

.red {
  color: #ff4d6d;
}

.section-grid {
  display: grid;
  gap: 10px;
}

.section-card {
  border: 1px solid rgba(33, 230, 255, 0.16);
  background: rgba(2, 16, 31, 0.56);
  border-radius: 8px;
  padding: 10px;
}

.section-card h4 {
  margin: 0 0 8px;
  color: #21e6ff;
  font-size: 13px;
}

.table-wrap {
  overflow: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.table th,
.table td {
  padding: 7px 6px;
  border-bottom: 1px solid rgba(33, 230, 255, 0.14);
  text-align: left;
}

.table th {
  color: #21e6ff;
  font-weight: 600;
  background: rgba(33, 230, 255, 0.08);
}

.table td {
  color: #78aeca;
}

.cloud-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.cloud-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border: 1px solid rgba(33, 230, 255, 0.12);
  border-radius: 6px;
  background: rgba(2, 16, 31, 0.38);
}

.cloud-item.wide {
  grid-column: 1 / -1;
}

.cloud-item span {
  color: #78aeca;
  font-size: 12px;
}

.cloud-item strong {
  color: #f2fbff;
  font-size: 13px;
}

.message-log {
  max-height: 180px;
  overflow: auto;
  border: 1px solid rgba(33, 230, 255, 0.16);
  border-radius: 8px;
  padding: 8px;
  background: rgba(1, 12, 23, 0.45);
}

.log-head {
  display: flex;
  justify-content: space-between;
  color: #78aeca;
  font-size: 12px;
  margin-bottom: 6px;
}

.log-line {
  display: grid;
  grid-template-columns: 54px 1fr;
  gap: 8px;
  font-size: 12px;
  color: #78aeca;
  padding: 5px 0;
  border-bottom: 1px dashed rgba(33, 230, 255, 0.12);
}

.log-line:last-child {
  border-bottom: none;
}

.log-line strong {
  color: #21e6ff;
  font-weight: 600;
}

.log-empty {
  margin: 0;
  color: #78aeca;
  font-size: 12px;
}

@media (max-width: 1320px) {
  .flow {
    grid-template-columns: 1fr;
  }

  .arrow {
    display: none;
  }
}
</style>
