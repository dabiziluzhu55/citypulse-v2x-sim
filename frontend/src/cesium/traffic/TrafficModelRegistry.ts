export type VehicleType = 'passenger' | 'bus' | 'truck'

export interface VehicleModelDefinition {
  type: VehicleType
  lengthMeters: number
  widthMeters: number
  heightMeters: number
  /** CSS 颜色，用于 2D 图标着色或 3D GLB 颜色混合 */
  color: string
  /** 可选的按类型 GLB 覆盖；为空时使用全局 GLB 或程序化低模 */
  modelUri?: string
  /** GLB 统一缩放（不同官方模型量纲不同，用于归一到真实车长） */
  modelScale: number
  /** 车头朝向修正（度），叠加到 SUMO 航向上 */
  headingOffsetDegrees: number
}

export const VEHICLE_DIMENSIONS: Record<VehicleType, { lengthMeters: number; widthMeters: number; heightMeters: number }> = {
  passenger: { lengthMeters: 4.6, widthMeters: 1.8, heightMeters: 1.5 },
  bus: { lengthMeters: 12, widthMeters: 2.5, heightMeters: 3.2 },
  truck: { lengthMeters: 8.5, widthMeters: 2.4, heightMeters: 3.0 },
}

const PASSENGER_COLORS = ['#d7e3ee', '#8fb8de', '#c7d2dc', '#a7c4dd', '#e6ebf0', '#9fb2c4']
const BUS_COLOR = '#3f7bd6'
const TRUCK_COLOR = '#c9954b'

/** 稳定哈希：同一 vehicle_id 在不同帧得到一致结果 */
function stableHash(value: string): number {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0
  }
  return Math.abs(hash)
}

function resolveType(vehicleId: string, laneId: string): VehicleType {
  const bucket = stableHash(`${vehicleId}|${laneId}`) % 100
  if (bucket < 8) return 'bus'
  if (bucket < 20) return 'truck'
  return 'passenger'
}

function resolveColor(type: VehicleType, vehicleId: string): string {
  if (type === 'bus') return BUS_COLOR
  if (type === 'truck') return TRUCK_COLOR
  const index = stableHash(vehicleId) % PASSENGER_COLORS.length
  return PASSENGER_COLORS[index] ?? PASSENGER_COLORS[0]
}

export interface TrafficModelRegistryOptions {
  /** 全局 GLB（所有类型共用），为空则程序化低模 */
  globalModelUri?: string
  /** 按类型的 GLB 覆盖 */
  modelUriByType?: Partial<Record<VehicleType, string>>
  /** 按类型的 GLB 缩放覆盖 */
  modelScaleByType?: Partial<Record<VehicleType, number>>
  headingOffsetDegrees?: number
}

/**
 * 车型注册表：按 vehicle_id + lane_id 稳定分配车型、颜色、真实米制尺寸、GLB。
 * 结果按 vehicle_id 缓存，保证跨帧一致。
 */
export class TrafficModelRegistry {
  private readonly cache = new Map<string, VehicleModelDefinition>()
  private readonly globalModelUri: string
  private readonly modelUriByType: Partial<Record<VehicleType, string>>
  private readonly modelScaleByType: Partial<Record<VehicleType, number>>
  private readonly headingOffsetDegrees: number

  constructor(options: TrafficModelRegistryOptions = {}) {
    this.globalModelUri = options.globalModelUri?.trim() ?? ''
    this.modelUriByType = options.modelUriByType ?? {}
    this.modelScaleByType = options.modelScaleByType ?? {}
    this.headingOffsetDegrees = options.headingOffsetDegrees ?? 0
  }

  resolve(vehicleId: string, laneId: string): VehicleModelDefinition {
    const cached = this.cache.get(vehicleId)
    if (cached) return cached

    const type = resolveType(vehicleId, laneId)
    const dimensions = VEHICLE_DIMENSIONS[type]
    const modelUri = this.modelUriByType[type]?.trim() || this.globalModelUri || undefined
    const modelScale = this.modelScaleByType[type] ?? 1

    const definition: VehicleModelDefinition = {
      type,
      lengthMeters: dimensions.lengthMeters,
      widthMeters: dimensions.widthMeters,
      heightMeters: dimensions.heightMeters,
      color: resolveColor(type, vehicleId),
      modelUri,
      modelScale,
      headingOffsetDegrees: this.headingOffsetDegrees,
    }
    this.cache.set(vehicleId, definition)
    return definition
  }

  hasModelUri(): boolean {
    return Boolean(this.globalModelUri) || Object.keys(this.modelUriByType).length > 0
  }

  clear(): void {
    this.cache.clear()
  }
}
