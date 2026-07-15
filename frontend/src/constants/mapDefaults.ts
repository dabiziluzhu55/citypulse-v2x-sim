import type { CesiumCameraPreset, CesiumCameraPresetId } from '../types/map'
import type { ScenarioTemplate } from '../types/scenario'

/** 雄安测试区域默认视野（WGS84: [lon, lat]） */
export const DEFAULT_MAP_CENTER: [number, number] = [115.9348, 39.0631]

export const DEFAULT_MAP_ZOOM = 13

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
    description: '低空贴近道路，突出道路发光层和建筑纵深。',
    height: 420,
    pitchDegrees: -22,
    headingDegrees: 35,
    rangeMultiplier: 0.55,
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

/** [minLon, minLat, maxLon, maxLat] */
export const XIONGAN_MAP_BOUNDS: [number, number, number, number] = [
  115.92696, 39.05825, 115.94267, 39.06798,
]

export type RoadVisualLevel = 'arterial' | 'secondary' | 'connector'

export interface RoadVisualSegment {
  id: string
  name: string
  level: RoadVisualLevel
  coordinates: Array<[number, number]>
}

export const XIONGAN_ROAD_VISUAL_SEGMENTS: RoadVisualSegment[] = [
  {
    id: 'east-west-main-1',
    name: '东西向主干道 A',
    level: 'arterial',
    coordinates: [
      [115.9274, 39.0649],
      [115.9305, 39.0647],
      [115.9348, 39.0645],
      [115.9392, 39.0642],
      [115.9422, 39.064],
    ],
  },
  {
    id: 'east-west-main-2',
    name: '东西向主干道 B',
    level: 'arterial',
    coordinates: [
      [115.928, 39.0608],
      [115.9315, 39.0609],
      [115.9357, 39.0611],
      [115.9394, 39.0613],
      [115.9416, 39.0614],
    ],
  },
  {
    id: 'north-south-main-1',
    name: '南北向主干道 A',
    level: 'arterial',
    coordinates: [
      [115.932, 39.0588],
      [115.9322, 39.0617],
      [115.9326, 39.0645],
      [115.933, 39.0673],
    ],
  },
  {
    id: 'north-south-main-2',
    name: '南北向主干道 B',
    level: 'arterial',
    coordinates: [
      [115.9382, 39.0586],
      [115.9379, 39.0612],
      [115.9375, 39.0643],
      [115.9371, 39.0672],
    ],
  },
  {
    id: 'secondary-school-loop',
    name: '学校片区环路',
    level: 'secondary',
    coordinates: [
      [115.9362, 39.0591],
      [115.9387, 39.059],
      [115.9406, 39.0605],
      [115.9401, 39.062],
      [115.9372, 39.0621],
      [115.9362, 39.0591],
    ],
  },
  {
    id: 'secondary-event-loop',
    name: '事件片区环路',
    level: 'secondary',
    coordinates: [
      [115.9294, 39.0643],
      [115.9316, 39.0665],
      [115.9347, 39.0666],
      [115.9354, 39.0648],
      [115.9294, 39.0643],
    ],
  },
  {
    id: 'connector-1',
    name: '联络道 1',
    level: 'connector',
    coordinates: [
      [115.9301, 39.0609],
      [115.9311, 39.0627],
      [115.9326, 39.0645],
    ],
  },
  {
    id: 'connector-2',
    name: '联络道 2',
    level: 'connector',
    coordinates: [
      [115.9357, 39.0611],
      [115.9367, 39.0627],
      [115.9375, 39.0643],
    ],
  },
  {
    id: 'connector-3',
    name: '联络道 3',
    level: 'connector',
    coordinates: [
      [115.9326, 39.0645],
      [115.9347, 39.0666],
      [115.9371, 39.0672],
    ],
  },
]

export interface TemplateMapViewport {
  center: [number, number]
  zoom: number
  bounds?: [number, number, number, number]
}

/** 各场景模板在雄安新区内的地理视野 */
export const TEMPLATE_MAP_REGISTRY: Record<string, TemplateMapViewport> = {
  xiongan20: {
    center: [115.9348, 39.0631],
    zoom: 15,
    bounds: [115.928, 39.059, 115.941, 39.067],
  },
  corridor4: {
    center: [115.9312, 39.0645],
    zoom: 16,
    bounds: [115.929, 39.0625, 115.934, 39.066],
  },
  school: {
    center: [115.9385, 39.0602],
    zoom: 16,
    bounds: [115.936, 39.058, 115.941, 39.062],
  },
  event: {
    center: [115.9325, 39.0662],
    zoom: 15,
    bounds: [115.929, 39.064, 115.936, 39.068],
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
