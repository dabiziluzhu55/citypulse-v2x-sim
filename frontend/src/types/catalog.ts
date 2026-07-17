export interface CatalogOrigin {
  origin_id: string
  label: string
  lane_ids: string[]
}

export interface CatalogLane {
  lane_id: string
  edge_id: string
  lane_index: number
  role: string
  approach: string | null
  approach_label: string | null
  length: number
  max_speed: number
}

export interface CatalogIntersection {
  intersection_id: string
  longitude: number | null
  latitude: number | null
  periods: string[]
  origins: CatalogOrigin[]
  lanes: CatalogLane[]
}

export interface FlowMultiplierRange {
  min: number
  max: number
}

export interface CatalogResponse {
  intersections: CatalogIntersection[]
  event_types: string[]
  control_modes: string[]
  flow_multiplier: FlowMultiplierRange
}
