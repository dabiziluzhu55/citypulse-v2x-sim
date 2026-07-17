import type {
  SimulationSnapshot,
} from '../types/simulation'
import type {
  TrafficIntersectionView,
  TrafficStateView,
  TrafficStatus,
  TrafficVehicleView,
} from '../types/traffic'

function resolveStatus(avgSpeed: number, halting: number): TrafficStatus {
  if (halting >= 8 || avgSpeed < 2) {
    return 'congested'
  }
  if (avgSpeed < 6) {
    return 'slow'
  }
  return 'free'
}

const PHASE_NAMES = ['相位一', '相位二', '相位三', '相位四', '相位五', '相位六']

function resolvePhaseName(phase: number, stage: string): string {
  const base = PHASE_NAMES[phase] ?? `相位${phase + 1}`
  const stageLabel =
    stage === 'GREEN' ? '绿灯' : stage === 'YELLOW' ? '黄灯' : stage === 'CLEARANCE' ? '清空' : stage
  return stageLabel ? `${base}·${stageLabel}` : base
}

export function snapshotToTrafficView(snapshot: SimulationSnapshot): TrafficStateView {
  const intersections: TrafficIntersectionView[] = Object.entries(
    snapshot.intersections ?? {},
  ).map(([id, runtime]) => {
    const lanes = Object.values(runtime.lanes ?? {})
    const laneCount = lanes.length || 1
    const halting = lanes.reduce((sum, lane) => sum + (lane.halting_count ?? 0), 0)
    const vehicleCount = lanes.reduce((sum, lane) => sum + (lane.vehicle_count ?? 0), 0)
    const avgWaiting =
      lanes.reduce((sum, lane) => sum + (lane.waiting_time ?? 0), 0) / laneCount
    const avgSpeed = lanes.reduce((sum, lane) => sum + (lane.mean_speed ?? 0), 0) / laneCount

    return {
      intersection_id: id,
      name: id,
      current_phase: runtime.current_phase,
      phase_name: resolvePhaseName(runtime.current_phase, runtime.stage),
      stage_elapsed: runtime.stage_elapsed,
      queue_length: halting,
      vehicle_count: vehicleCount,
      avg_waiting_time: avgWaiting,
      avg_speed: avgSpeed,
      status: resolveStatus(avgSpeed, halting),
    }
  })

  const vehicles: TrafficVehicleView[] = (snapshot.vehicles ?? []).map((vehicle) => ({
    vehicle_id: vehicle.vehicle_id,
    longitude: vehicle.longitude,
    latitude: vehicle.latitude,
    x: vehicle.x,
    y: vehicle.y,
    speed: vehicle.speed,
    angle: vehicle.angle,
    lane_id: vehicle.lane_id,
  }))

  return {
    session_id: snapshot.session_id,
    elapsed_seconds: snapshot.elapsed_seconds,
    duration_seconds: snapshot.duration_seconds,
    progress: snapshot.progress,
    official_time: snapshot.official_time,
    intersections,
    vehicles,
    metrics: snapshot.metrics ?? null,
  }
}
