export type NetworkSource = 'osm_import' | 'prebuilt_sumo' | 'manual_netedit'

export type TrafficFlowMode = 'flat' | 'morning_peak' | 'evening_peak' | 'event_dispersal'

export type TrafficLightPlan = 'fixed_time' | 'default_sumo' | 'custom'

export type DisturbanceType = 'lane_closure' | 'accident' | 'event_dispersal' | 'speed_limit'

export interface ScenarioTemplate {
  template_id: string
  name: string
  intersection_count: number
  description: string
  map_center?: [number, number]
  map_bounds?: [number, number, number, number]
  default_zoom?: number
}

export interface ScenarioTemplatesResponse {
  templates: ScenarioTemplate[]
}

export interface VehicleTypeRatio {
  car: number
  bus: number
  truck: number
  bike: number
}

export interface TrafficFlowConfig {
  mode: TrafficFlowMode
  flow_scale: number
  vehicle_types: VehicleTypeRatio
  duration: number
}

export interface OdGroup {
  od_id: string
  origin: string
  destination: string
  vehicles_per_hour: number
  start_time: number
  end_time: number
}

export interface TrafficLightConfig {
  initial_plan: TrafficLightPlan
  cycle_length: number
  min_green: number
  max_green: number
  yellow_time: number
}

export interface LaneClosureDisturbance {
  type: 'lane_closure'
  edge_id: string
  lane_id: string
  start_time: number
  duration: number
}

export interface AccidentDisturbance {
  type: 'accident'
  vehicle_id?: string
  random_vehicle: boolean
  edge_id: string
  start_time: number
  duration: number
}

export interface EventDispersalDisturbance {
  type: 'event_dispersal'
  origin: string
  destination: string
  surge_flow: number
  start_time: number
}

export interface SpeedLimitDisturbance {
  type: 'speed_limit'
  edge_id: string
  speed_limit: number
  start_time: number
  duration: number
}

export type DisturbanceEvent =
  | LaneClosureDisturbance
  | AccidentDisturbance
  | EventDispersalDisturbance
  | SpeedLimitDisturbance

export interface CreateScenarioRequest {
  name: string
  template_id: string
  network_source: NetworkSource
  traffic_flow: TrafficFlowConfig
  od_groups: OdGroup[]
  traffic_light: TrafficLightConfig
  disturbances: DisturbanceEvent[]
}

export interface CreateScenarioResponse {
  scenario_id: string
  status: string
  files: {
    net: string
    route: string
    config: string
  }
}

export interface NetworkConfigForm {
  template_id: string
  network_source: NetworkSource
}
