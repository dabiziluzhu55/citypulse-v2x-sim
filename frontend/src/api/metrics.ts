import { apiClient } from './client'
import type {
  ExperimentComparisonResponse,
  MetricsTimeseriesResponse,
  RealtimeMetricsResponse,
} from '../types/metrics'

export async function fetchRealtimeMetrics(runId: string): Promise<RealtimeMetricsResponse> {
  const { data } = await apiClient.get<RealtimeMetricsResponse>(`/runs/${runId}/metrics/realtime`)
  return data
}

export async function fetchExperimentComparison(
  experimentId: string,
): Promise<ExperimentComparisonResponse> {
  const { data } = await apiClient.get<ExperimentComparisonResponse>(
    `/experiments/${experimentId}/comparison`,
  )
  return data
}

export async function fetchMetricsTimeseries(runId: string): Promise<MetricsTimeseriesResponse> {
  const { data } = await apiClient.get<MetricsTimeseriesResponse>(
    `/runs/${runId}/metrics/timeseries`,
  )
  return data
}
