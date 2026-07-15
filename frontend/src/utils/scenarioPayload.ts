import type { CreateScenarioRequest, DisturbanceEvent } from '../types/scenario'

export function buildSubmitPayload(form: CreateScenarioRequest): CreateScenarioRequest {
  return {
    name: form.name.trim(),
    template_id: form.template_id,
    network_source: form.network_source,
    traffic_flow: {
      mode: form.traffic_flow.mode,
      flow_scale: form.traffic_flow.flow_scale,
      vehicle_types: { ...form.traffic_flow.vehicle_types },
      duration: form.traffic_flow.duration,
    },
    od_groups: form.od_groups.map((od) => ({
      od_id: od.od_id,
      origin: od.origin,
      destination: od.destination,
      vehicles_per_hour: od.vehicles_per_hour,
      start_time: od.start_time,
      end_time: od.end_time,
    })),
    traffic_light: { ...form.traffic_light },
    disturbances: form.disturbances.map((item) => serializeDisturbance(item)),
  }
}

function serializeDisturbance(disturbance: DisturbanceEvent): DisturbanceEvent {
  switch (disturbance.type) {
    case 'lane_closure':
      return {
        type: 'lane_closure',
        edge_id: disturbance.edge_id.trim(),
        lane_id: disturbance.lane_id.trim(),
        start_time: disturbance.start_time,
        duration: disturbance.duration,
      }
    case 'accident': {
      const payload: DisturbanceEvent = {
        type: 'accident',
        random_vehicle: disturbance.random_vehicle,
        edge_id: disturbance.edge_id.trim(),
        start_time: disturbance.start_time,
        duration: disturbance.duration,
      }
      if (!disturbance.random_vehicle && disturbance.vehicle_id?.trim()) {
        return { ...payload, vehicle_id: disturbance.vehicle_id.trim() }
      }
      if (disturbance.vehicle_id?.trim()) {
        return { ...payload, vehicle_id: disturbance.vehicle_id.trim() }
      }
      return payload
    }
    case 'event_dispersal':
      return {
        type: 'event_dispersal',
        origin: disturbance.origin,
        destination: disturbance.destination,
        surge_flow: disturbance.surge_flow,
        start_time: disturbance.start_time,
      }
    case 'speed_limit':
      return {
        type: 'speed_limit',
        edge_id: disturbance.edge_id.trim(),
        speed_limit: disturbance.speed_limit,
        start_time: disturbance.start_time,
        duration: disturbance.duration,
      }
    default:
      return disturbance
  }
}

export function createDefaultForm(): CreateScenarioRequest {
  return {
    name: '',
    template_id: '',
    network_source: 'prebuilt_sumo',
    traffic_flow: {
      mode: 'morning_peak',
      flow_scale: 1.2,
      vehicle_types: { car: 0.75, bus: 0.1, truck: 0.1, bike: 0.05 },
      duration: 3600,
    },
    od_groups: [
      {
        od_id: 'od_001',
        origin: 'residential_area_A',
        destination: 'office_area_B',
        vehicles_per_hour: 800,
        start_time: 0,
        end_time: 3600,
      },
    ],
    traffic_light: {
      initial_plan: 'fixed_time',
      cycle_length: 90,
      min_green: 10,
      max_green: 60,
      yellow_time: 3,
    },
    disturbances: [],
  }
}

export function createDefaultDisturbance(type: DisturbanceEvent['type']): DisturbanceEvent {
  switch (type) {
    case 'lane_closure':
      return {
        type: 'lane_closure',
        edge_id: 'E12',
        lane_id: 'E12_0',
        start_time: 600,
        duration: 900,
      }
    case 'accident':
      return {
        type: 'accident',
        vehicle_id: '',
        random_vehicle: true,
        edge_id: 'E08',
        start_time: 1200,
        duration: 600,
      }
    case 'event_dispersal':
      return {
        type: 'event_dispersal',
        origin: 'school',
        destination: 'main_road_exit',
        surge_flow: 1200,
        start_time: 3000,
      }
    case 'speed_limit':
      return {
        type: 'speed_limit',
        edge_id: 'E15',
        speed_limit: 8.33,
        start_time: 0,
        duration: 1800,
      }
    default:
      return {
        type: 'lane_closure',
        edge_id: '',
        lane_id: '',
        start_time: 0,
        duration: 300,
      }
  }
}

export function createOdGroup(index: number, duration: number) {
  return {
    od_id: `od_${String(index).padStart(3, '0')}`,
    origin: 'residential_area_A',
    destination: 'office_area_B',
    vehicles_per_hour: 800,
    start_time: 0,
    end_time: duration,
  }
}
