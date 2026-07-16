import type { CollaborationLogEntry } from '../types/collaboration'
import type { MetricsTimeseriesResponse } from '../types/metrics'

export const MOCK_RUN_ID = 'run_20260704_001'

export const MOCK_COLLABORATION_LOG_ENTRIES: CollaborationLogEntry[] = [
  {
    id: 'mock-cloud-1',
    timeLabel: '20:35:12',
    source: 'cloud',
    message: '决策：走廊优先，南北走廊排队超阈值',
  },
  {
    id: 'mock-j12-1',
    timeLabel: '20:35:10',
    source: 'J12',
    message: '上报：相位 2，排队 35 辆，等待 64.2s',
  },
  {
    id: 'mock-j12-check-1',
    timeLabel: '20:35:08',
    source: 'J12',
    message: '校验：满足最小绿灯约束，允许执行',
  },
  {
    id: 'mock-sumo-1',
    timeLabel: '20:35:06',
    source: 'SUMO',
    message: 'J12 延长绿灯 10s',
  },
  {
    id: 'mock-veh-1',
    timeLabel: '20:35:04',
    source: 'veh_1024',
    message: '反馈：建议速度 10 m/s，当前 9.5 m/s',
  },
  {
    id: 'mock-j09-1',
    timeLabel: '20:34:58',
    source: 'J09',
    message: '上报：相位 1，排队 22 辆，等待 38.6s',
  },
  {
    id: 'mock-cloud-2',
    timeLabel: '20:34:52',
    source: 'cloud',
    message: '决策：区域协调，下发 J09-J12 绿波方案',
  },
  {
    id: 'mock-veh-2',
    timeLabel: '20:34:48',
    source: 'veh_2048',
    message: '反馈：建议路径 J12→J16，当前 8.2 m/s',
  },
]

export function createMockMetricsTimeseries(runId = MOCK_RUN_ID): MetricsTimeseriesResponse {
  return {
    run_id: runId,
    series: [
      { time: 0, avg_waiting_time: 0, avg_queue_length: 0, throughput: 0 },
      { time: 300, avg_waiting_time: 18.2, avg_queue_length: 10.4, throughput: 220 },
      { time: 600, avg_waiting_time: 28.6, avg_queue_length: 14.1, throughput: 680 },
      { time: 900, avg_waiting_time: 35.4, avg_queue_length: 16.2, throughput: 980 },
      { time: 1250, avg_waiting_time: 42.5, avg_queue_length: 18.3, throughput: 1370 },
    ],
  }
}
