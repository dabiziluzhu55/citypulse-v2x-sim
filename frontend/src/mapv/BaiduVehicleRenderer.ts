import * as mapvthree from '@baidumap/mapv-three'
import type { TrafficVehicleView } from '../types/traffic'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'
import type { RoadCoordinateProjector } from './roadGeometry'
import {
  resolveVehicleRenderRadius,
  selectVisibleVehicles,
} from './vehicleVisibility'
import { createVehicleTwinSample } from './vehicleTwinSample'

const MAX_TWIN_UPDATES_PER_SECOND = 4
const TWIN_INTERPOLATION_DELAY_MS = 350
const INITIAL_SAMPLE_LEAD_MS = 250

export interface VehicleRenderStats {
  inputCount: number
  visibleCount: number
  radiusMeters: number
}

export class BaiduVehicleRenderer {
  private readonly engine: mapvthree.Engine
  private readonly twin: mapvthree.Twin
  private readonly projector: RoadCoordinateProjector
  private lastUpdateAt = 0
  private lastVehicles: TrafficVehicleView[] = []
  private visibleCount = 0
  private primed = false

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.engine = engine
    this.projector = projector
    this.twin = engine.add(new mapvthree.Twin({
      delay: TWIN_INTERPOLATION_DELAY_MS,
      modelConfig: {
        3: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.CAR,
        6: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.BUS,
        10: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.TRUCK,
      },
      keepSize: false,
      maxScale: 20,
    }))
  }

  update(vehicles: TrafficVehicleView[], force = false): VehicleRenderStats {
    this.lastVehicles = vehicles
    const time = Date.now()
    const cameraRange = this.engine.map.getRange()
    const radiusMeters = resolveVehicleRenderRadius(cameraRange)
    if (!force && time - this.lastUpdateAt < 1000 / MAX_TWIN_UPDATES_PER_SECOND) {
      return { inputCount: vehicles.length, visibleCount: this.visibleCount, radiusMeters }
    }
    this.lastUpdateAt = time
    const visible = selectVisibleVehicles(
      vehicles,
      this.projector,
      this.engine.map.getCenter(),
      cameraRange,
    )
    this.visibleCount = visible.length
    if (visible.length === 0) {
      if (this.primed) this.clear()
      return { inputCount: vehicles.length, visibleCount: 0, radiusMeters }
    }
    const samples = visible.map(({ vehicle, longitude, latitude }) => (
      createVehicleTwinSample(
        vehicle,
        longitude,
        latitude,
        time,
        this.resolveModelType(vehicle.lane_id),
      )
    ))
    if (!this.primed) {
      this.twin.push(samples.map((sample) => ({
        ...sample,
        time: time - INITIAL_SAMPLE_LEAD_MS,
      })))
      this.primed = true
    }
    this.twin.push(samples)
    this.engine.requestRender()
    return { inputCount: vehicles.length, visibleCount: visible.length, radiusMeters }
  }

  refreshViewport(): VehicleRenderStats {
    return this.update(this.lastVehicles, true)
  }

  private resolveModelType(laneId: string): number {
    const hash = [...laneId].reduce((value, character) => value + character.charCodeAt(0), 0)
    return hash % 17 === 0 ? 6 : hash % 11 === 0 ? 10 : 3
  }

  clear(): void {
    this.lastUpdateAt = 0
    this.visibleCount = 0
    this.primed = false
    this.twin.reset()
  }

  destroy(): void {
    this.clear()
    this.engine.remove(this.twin)
  }
}
