export interface HealthResponse {
  status: 'ok' | 'degraded'
  app: string
  sumo_home_configured: boolean
  generated_artifacts_ready: boolean
  simulation_manager_ready: boolean
  missing_files?: string[]
}
