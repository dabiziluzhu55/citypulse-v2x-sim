import type { CesiumCameraPreset, CesiumCameraPresetId } from '../types/map'
import type { ScenarioTemplate } from '../types/scenario'

/**
 * 3D Tiles 真实中心（从 tileset.json transform 反算）：
 *   lon=115.954990  lat=38.986486
 * 子节点分布范围：
 *   LON 115.797~116.113  LAT 38.712~39.154
 *   实际密集建筑区中心：lon≈115.981  lat≈38.985
 */

/** 地图默认中心对齐 3D Tiles 真实中心（WGS84: [lon, lat]） */
export const DEFAULT_MAP_CENTER: [number, number] = [115.981, 38.985]

export const DEFAULT_MAP_ZOOM = 14

/** Cesium 相机默认高度（米） */
export const DEFAULT_CESIUM_CAMERA_HEIGHT = 2000

export const DEFAULT_CESIUM_CAMERA_PRESET_ID: CesiumCameraPresetId = 'overview'

export const CESIUM_CAMERA_PRESETS: CesiumCameraPreset[] = [
  {
    id: 'overview',
    label: '总览视角',
    shortLabel: '总览',
    description: '覆盖雄安测试区全局，适合查看建筑和道路整体态势。',
    height: 2200,
    pitchDegrees: -55,
    headingDegrees: 0,
    rangeMultiplier: 1.25,
  },
  {
    id: 'birdseye',
    label: '倾斜鸟瞰',
    shortLabel: '鸟瞰',
    description: '增强空间层次和建筑立面观感。',
    height: 1400,
    pitchDegrees: -48,
    headingDegrees: 35,
    rangeMultiplier: 1,
  },
  {
    id: 'traffic-top',
    label: '交通俯视',
    shortLabel: '俯视',
    description: '接近监控视角，便于观察路网和交通状态。',
    height: 1000,
    pitchDegrees: -80,
    headingDegrees: 0,
    rangeMultiplier: 0.9,
  },
  {
    id: 'road-cruise',
    label: '道路巡航',
    shortLabel: '巡航',
    description: '低空聚焦局部建筑群，减少远端瓦片加载并提升巡航稳定性。',
    height: 720,
    pitchDegrees: -35,
    headingDegrees: 35,
    rangeMultiplier: 0.55,
    maxCameraHeight: 800,
    localViewRadiusMeters: 1000,
    minimumZoomDistance: 150,
    maximumZoomDistance: 1600,
  },
  {
    id: 'intersection',
    label: '路口观察',
    shortLabel: '路口',
    description: '面向关键路口的低空观察视角。',
    height: 260,
    pitchDegrees: -32,
    headingDegrees: 60,
    rangeMultiplier: 0.45,
  },
]

export function resolveCesiumCameraPreset(id: CesiumCameraPresetId): CesiumCameraPreset {
  return CESIUM_CAMERA_PRESETS.find((preset) => preset.id === id) ?? CESIUM_CAMERA_PRESETS[0]
}

/** Cesium ion OSM Buildings 资产 ID */
export const CESIUM_OSM_BUILDINGS_ASSET_ID = 96188

/** 本地雄安彩色建筑 3D Tiles 默认入口 */
export const XIONGAN_3DTILES_DEFAULT_URL = '/3dtiles/xiongan/tileset.json'

export interface TilesetCalibration {
  eastMeters: number
  northMeters: number
  upMeters: number
  headingDegrees: number
  scale: number
}

/** 先保留源 tileset 的 ECEF/ENU 定位，测量后再填写修正量。 */
export const XIONGAN_3DTILES_CALIBRATION: TilesetCalibration = {
  eastMeters: 0,
  northMeters: 0,
  upMeters: 0,
  headingDegrees: 0,
  scale: 1,
}

/**
 * 地图边界对齐 3D Tiles 子节点实际分布范围：
 *   LON 115.797~116.113  LAT 38.712~39.154
 * 取密集建筑核心区（约 5km 半径）作为默认边界。
 * [minLon, minLat, maxLon, maxLat]
 */
export const XIONGAN_MAP_BOUNDS: [number, number, number, number] = [
  115.936, 38.951, 116.026, 39.019,
]

export type RoadVisualLevel = 'arterial' | 'secondary' | 'connector'

export interface RoadVisualSegment {
  id: string
  name: string
  level: RoadVisualLevel
  coordinates: Array<[number, number]>
}

/**
 * 道路骨架坐标已对齐 3D Tiles 真实中心（115.981, 38.985）。
 * 坐标依据 tileset.json 中心点 + 标准城市路网间距估算：
 *   主干道间距约 500m，次干道约 300m，联络道约 200m。
 */
export const XIONGAN_ROAD_VISUAL_SEGMENTS: RoadVisualSegment[] = [
  {
    id: 'ew-arterial-north',
    name: '东西向主干道 北',
    level: 'arterial',
    coordinates: [
      [115.946, 39.001],
      [115.958, 39.001],
      [115.970, 39.001],
      [115.981, 39.001],
      [115.993, 39.001],
      [116.005, 39.001],
      [116.016, 39.001],
    ],
  },
  {
    id: 'ew-arterial-center',
    name: '东西向主干道 中',
    level: 'arterial',
    coordinates: [
      [115.946, 38.985],
      [115.958, 38.985],
      [115.970, 38.985],
      [115.981, 38.985],
      [115.993, 38.985],
      [116.005, 38.985],
      [116.016, 38.985],
    ],
  },
  {
    id: 'ew-arterial-south',
    name: '东西向主干道 南',
    level: 'arterial',
    coordinates: [
      [115.946, 38.969],
      [115.958, 38.969],
      [115.970, 38.969],
      [115.981, 38.969],
      [115.993, 38.969],
      [116.005, 38.969],
      [116.016, 38.969],
    ],
  },
  {
    id: 'ns-arterial-west',
    name: '南北向主干道 西',
    level: 'arterial',
    coordinates: [
      [115.958, 38.960],
      [115.958, 38.969],
      [115.958, 38.977],
      [115.958, 38.985],
      [115.958, 38.993],
      [115.958, 39.001],
      [115.958, 39.010],
    ],
  },
  {
    id: 'ns-arterial-center',
    name: '南北向主干道 中',
    level: 'arterial',
    coordinates: [
      [115.981, 38.960],
      [115.981, 38.969],
      [115.981, 38.977],
      [115.981, 38.985],
      [115.981, 38.993],
      [115.981, 39.001],
      [115.981, 39.010],
    ],
  },
  {
    id: 'ns-arterial-east',
    name: '南北向主干道 东',
    level: 'arterial',
    coordinates: [
      [116.005, 38.960],
      [116.005, 38.969],
      [116.005, 38.977],
      [116.005, 38.985],
      [116.005, 38.993],
      [116.005, 39.001],
      [116.005, 39.010],
    ],
  },
  {
    id: 'ew-secondary-1',
    name: '东西向次干道 A',
    level: 'secondary',
    coordinates: [
      [115.958, 38.977],
      [115.970, 38.977],
      [115.981, 38.977],
      [115.993, 38.977],
      [116.005, 38.977],
    ],
  },
  {
    id: 'ew-secondary-2',
    name: '东西向次干道 B',
    level: 'secondary',
    coordinates: [
      [115.958, 38.993],
      [115.970, 38.993],
      [115.981, 38.993],
      [115.993, 38.993],
      [116.005, 38.993],
    ],
  },
  {
    id: 'ns-secondary-1',
    name: '南北向次干道 A',
    level: 'secondary',
    coordinates: [
      [115.970, 38.969],
      [115.970, 38.977],
      [115.970, 38.985],
      [115.970, 38.993],
      [115.970, 39.001],
    ],
  },
  {
    id: 'ns-secondary-2',
    name: '南北向次干道 B',
    level: 'secondary',
    coordinates: [
      [115.993, 38.969],
      [115.993, 38.977],
      [115.993, 38.985],
      [115.993, 38.993],
      [115.993, 39.001],
    ],
  },
  {
    id: 'connector-nw',
    name: '联络道 西北',
    level: 'connector',
    coordinates: [
      [115.958, 39.001],
      [115.964, 39.005],
      [115.970, 39.001],
    ],
  },
  {
    id: 'connector-ne',
    name: '联络道 东北',
    level: 'connector',
    coordinates: [
      [115.993, 39.001],
      [115.999, 39.005],
      [116.005, 39.001],
    ],
  },
  {
    id: 'connector-sw',
    name: '联络道 西南',
    level: 'connector',
    coordinates: [
      [115.958, 38.969],
      [115.964, 38.965],
      [115.970, 38.969],
    ],
  },
  {
    id: 'connector-se',
    name: '联络道 东南',
    level: 'connector',
    coordinates: [
      [115.993, 38.969],
      [115.999, 38.965],
      [116.005, 38.969],
    ],
  },
]

export interface TemplateMapViewport {
  center: [number, number]
  zoom: number
  bounds?: [number, number, number, number]
}

/** 各场景模板视野对齐 3D Tiles 真实位置 */
export const TEMPLATE_MAP_REGISTRY: Record<string, TemplateMapViewport> = {
  demo_2: {
    center: [116.126756, 38.99115],
    zoom: 17,
    bounds: [116.1198, 38.9858, 116.1337, 38.9965],
  },
  xiongan20: {
    center: [115.981, 38.985],
    zoom: 15,
    bounds: [115.936, 38.951, 116.026, 39.019],
  },
  corridor4: {
    center: [115.970, 38.985],
    zoom: 16,
    bounds: [115.952, 38.972, 115.988, 38.998],
  },
  school: {
    center: [116.000, 38.972],
    zoom: 16,
    bounds: [115.986, 38.962, 116.014, 38.982],
  },
  event: {
    center: [115.981, 39.001],
    zoom: 15,
    bounds: [115.960, 38.990, 116.002, 39.012],
  },
}

export function resolveTemplateMapViewport(templateId: string): TemplateMapViewport {
  return TEMPLATE_MAP_REGISTRY[templateId] ?? TEMPLATE_MAP_REGISTRY.xiongan20
}

export function enrichScenarioTemplate(template: ScenarioTemplate): ScenarioTemplate {
  const fallback = resolveTemplateMapViewport(template.template_id)
  return {
    ...template,
    map_center: template.map_center ?? fallback.center,
    map_bounds: template.map_bounds ?? fallback.bounds,
    default_zoom: template.default_zoom ?? fallback.zoom,
  }
}
