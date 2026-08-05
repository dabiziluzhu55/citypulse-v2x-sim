export interface HealthResponse {
  status: 'ok' | 'degraded'
  app: string
  simulation_manager_mode?: 'local' | 'redis'
  sumo_home_configured: boolean
  generated_artifacts_ready: boolean
  simulation_manager_ready: boolean
  redis_ready?: boolean
  redis_error?: string | null
  session_root_ready?: boolean
  missing_files?: string[]
}
