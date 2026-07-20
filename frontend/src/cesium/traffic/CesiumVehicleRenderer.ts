import * as Cesium from 'cesium'
import type { TrafficVehicleView } from '../../types/traffic'
import {
  TrafficModelRegistry,
  type VehicleModelDefinition,
} from './TrafficModelRegistry'
import {
  CESIUM_VEHICLE_HEADING_OFFSET_DEGREES,
  CESIUM_VEHICLE_HEIGHT_OFFSET_METERS,
  CESIUM_VEHICLE_INTERPOLATION_DELAY_SECONDS,
  CESIUM_VEHICLE_MODEL_SCALES,
  CESIUM_VEHICLE_MODEL_URIS,
  CESIUM_VEHICLE_POINT_LOD_DISTANCE,
  CESIUM_VEHICLE_STALE_SECONDS,
} from '../../constants/cesiumTrafficVisualization'

interface VehicleEntry {
  entity: Cesium.Entity
  position: Cesium.SampledPositionProperty
  definition: VehicleModelDefinition
  lastUpdateSeconds: number
}

/**
 * 三维车辆渲染器。
 *
 * 借鉴 SampledPositionProperty + VelocityOrientationProperty 的轨迹运动思路，
 * 但针对实时仿真做「滑动窗口采样」改造：每次 snapshot 到达时把最新位置
 * addSample 到时钟当前时间+插值延迟，Cesium 在两次 snapshot 之间自动插值，
 * 使车辆平滑移动；朝向由运动速度方向自动计算。
 */
export class CesiumVehicleRenderer {
  private static readonly EPOCH = Cesium.JulianDate.fromIso8601('2000-01-01T00:00:00Z')

  private readonly viewer: Cesium.Viewer
  private readonly dataSource: Cesium.CustomDataSource
  private readonly registry: TrafficModelRegistry
  private readonly entries = new Map<string, VehicleEntry>()

  constructor(viewer: Cesium.Viewer) {
    this.viewer = viewer
    this.dataSource = new Cesium.CustomDataSource('citypulse-vehicles')
    void this.viewer.dataSources.add(this.dataSource)
    this.registry = new TrafficModelRegistry({
      modelUriByType: CESIUM_VEHICLE_MODEL_URIS,
      modelScaleByType: CESIUM_VEHICLE_MODEL_SCALES,
      headingOffsetDegrees: CESIUM_VEHICLE_HEADING_OFFSET_DEGREES,
    })
    this.ensureRealtimeClock()
  }

  /** 保证时钟处于实时推进模式，供 SampledPositionProperty 插值使用 */
  private ensureRealtimeClock(): void {
    const clock = this.viewer.clock
    clock.shouldAnimate = true
    clock.clockStep = Cesium.ClockStep.SYSTEM_CLOCK
  }

  private secondsFromEpoch(time: Cesium.JulianDate): number {
    return Cesium.JulianDate.secondsDifference(time, CesiumVehicleRenderer.EPOCH)
  }

  /** 用最新一帧车辆快照更新渲染 */
  update(vehicles: TrafficVehicleView[]): void {
    const clockTime = this.viewer.clock.currentTime
    const nowSeconds = this.secondsFromEpoch(clockTime)
    const sampleTime = Cesium.JulianDate.addSeconds(
      clockTime,
      CESIUM_VEHICLE_INTERPOLATION_DELAY_SECONDS,
      new Cesium.JulianDate(),
    )
    const seen = new Set<string>()

    for (const vehicle of vehicles) {
      if (vehicle.longitude == null || vehicle.latitude == null) continue
      seen.add(vehicle.vehicle_id)
      this.upsertVehicle(vehicle, sampleTime, nowSeconds)
    }

    this.pruneStale(seen, nowSeconds)
    this.viewer.scene.requestRender()
  }

  private upsertVehicle(
    vehicle: TrafficVehicleView,
    sampleTime: Cesium.JulianDate,
    nowSeconds: number,
  ): void {
    const position = Cesium.Cartesian3.fromDegrees(
      vehicle.longitude as number,
      vehicle.latitude as number,
      CESIUM_VEHICLE_HEIGHT_OFFSET_METERS,
    )

    const existing = this.entries.get(vehicle.vehicle_id)
    if (existing) {
      existing.position.addSample(sampleTime, position)
      existing.lastUpdateSeconds = nowSeconds
      return
    }

    const definition = this.registry.resolve(vehicle.vehicle_id, vehicle.lane_id)
    const sampled = new Cesium.SampledPositionProperty()
    sampled.forwardExtrapolationType = Cesium.ExtrapolationType.HOLD
    sampled.backwardExtrapolationType = Cesium.ExtrapolationType.HOLD
    sampled.setInterpolationOptions({
      interpolationDegree: 1,
      interpolationAlgorithm: Cesium.LinearApproximation as unknown as Cesium.InterpolationAlgorithm,
    })
    const priorTime = Cesium.JulianDate.addSeconds(sampleTime, -0.5, new Cesium.JulianDate())
    sampled.addSample(priorTime, position)
    sampled.addSample(sampleTime, position)

    const entity = this.createEntity(vehicle.vehicle_id, sampled, definition)
    this.entries.set(vehicle.vehicle_id, {
      entity,
      position: sampled,
      definition,
      lastUpdateSeconds: nowSeconds,
    })
  }

  private createEntity(
    vehicleId: string,
    position: Cesium.SampledPositionProperty,
    definition: VehicleModelDefinition,
  ): Cesium.Entity {
    const color = Cesium.Color.fromCssColorString(definition.color)
    const orientation = new Cesium.VelocityOrientationProperty(position)

    const entity = this.dataSource.entities.add({
      id: `vehicle-${vehicleId}`,
      position,
      orientation,
    })

    if (definition.modelUri) {
      entity.model = new Cesium.ModelGraphics({
        uri: definition.modelUri,
        scale: definition.modelScale,
        minimumPixelSize: 24,
        maximumScale: 80,
        color,
        colorBlendMode: Cesium.ColorBlendMode.MIX,
        colorBlendAmount: 0.35,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(
          0,
          CESIUM_VEHICLE_POINT_LOD_DISTANCE,
        ),
      })
    } else {
      entity.box = new Cesium.BoxGraphics({
        dimensions: new Cesium.Cartesian3(
          definition.widthMeters,
          definition.lengthMeters,
          definition.heightMeters,
        ),
        material: color.withAlpha(0.95),
        outline: false,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(
          0,
          CESIUM_VEHICLE_POINT_LOD_DISTANCE,
        ),
      })
    }

    entity.point = new Cesium.PointGraphics({
      pixelSize: 6,
      color,
      outlineColor: Cesium.Color.fromCssColorString('#04121f'),
      outlineWidth: 1,
      distanceDisplayCondition: new Cesium.DistanceDisplayCondition(
        CESIUM_VEHICLE_POINT_LOD_DISTANCE,
        Number.MAX_VALUE,
      ),
    })

    return entity
  }

  private pruneStale(seen: Set<string>, nowSeconds: number): void {
    for (const [vehicleId, entry] of this.entries) {
      if (seen.has(vehicleId)) continue
      if (nowSeconds - entry.lastUpdateSeconds >= CESIUM_VEHICLE_STALE_SECONDS) {
        this.dataSource.entities.remove(entry.entity)
        this.entries.delete(vehicleId)
      }
    }
  }

  clear(): void {
    this.dataSource.entities.removeAll()
    this.entries.clear()
    this.registry.clear()
  }

  destroy(): void {
    this.clear()
    this.viewer.dataSources.remove(this.dataSource, true)
  }
}
