import type { ScenarioTemplate } from '../types/scenario'



/** 雄安测试区域默认视野（WGS84: [lon, lat]） */

export const DEFAULT_MAP_CENTER: [number, number] = [115.9348, 39.0631]



export const DEFAULT_MAP_ZOOM = 13



/** Cesium 相机默认高度（米） */

export const DEFAULT_CESIUM_CAMERA_HEIGHT = 2000



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

