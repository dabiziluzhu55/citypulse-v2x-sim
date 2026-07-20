import * as mapvthree from '@baidumap/mapv-three'
import type { TrafficVehicleView } from '../types/traffic'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'

const MAX_TWIN_UPDATES_PER_SECOND = 10

export class BaiduVehicleRenderer {
  private readonly engine: mapvthree.Engine
  private readonly twin: mapvthree.Twin
  private lastUpdateAt = 0

  constructor(engine: mapvthree.Engine) {
    this.engine = engine
    this.twin = engine.add(new mapvthree.Twin({
      delay: 800,
      modelConfig: {
        3: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.CAR,
        6: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.BUS,
        10: mapvthree.twinConstants.REALISTIC_TEMPLATE_MODEL.TRUCK,
      },
      keepSize: false,
      maxScale: 20,
    }))
  }

  update(vehicles: TrafficVehicleView[]): void {
    const time = Date.now()
    if (time - this.lastUpdateAt < 1000 / MAX_TWIN_UPDATES_PER_SECOND) return
    this.lastUpdateAt = time
    this.twin.push(vehicles.flatMap((vehicle) => {
      if (vehicle.longitude == null || vehicle.latitude == null) return []
      const [lng, lat] = projectSimulationCoordinateToBaiduMap([
        vehicle.longitude,
        vehicle.latitude,
      ])
      return [{
        id: vehicle.vehicle_id,
        lng,
        lat,
        dir: vehicle.angle * Math.PI / 180,
        time,
        modelType: this.resolveModelType(vehicle.lane_id),
      }]
    }))
    this.engine.requestRender()
  }

  private resolveModelType(laneId: string): number {
    const hash = [...laneId].reduce((value, character) => value + character.charCodeAt(0), 0)
    return hash % 17 === 0 ? 6 : hash % 11 === 0 ? 10 : 3
  }

  clear(): void {
    this.lastUpdateAt = 0
    this.twin.reset()
  }

  destroy(): void {
    this.clear()
    this.engine.remove(this.twin)
  }
}
