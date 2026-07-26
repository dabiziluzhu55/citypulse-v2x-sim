import type BaseLayer from 'ol/layer/Base'
import LayerGroup from 'ol/layer/Group'
import TileLayer from 'ol/layer/Tile'
import OSM from 'ol/source/OSM'
import XYZ from 'ol/source/XYZ'
import type { TileCoord } from 'ol/tilecoord'

export type BasemapVariant = 'tianditu' | 'osm' | 'carto_dark' | 'carto_dark_nolabels'

export interface BasemapOption {
  id: BasemapVariant
  label: string
  description: string
}

/** 可选底图，默认天地图影像适合全站背景 */
export const BASEMAP_OPTIONS: BasemapOption[] = [
  {
    id: 'tianditu',
    label: '天地图影像',
    description: '国家地理信息公共服务平台影像 + 中文注记，国内可达',
  },
  {
    id: 'carto_dark_nolabels',
    label: 'Carto 暗色（无标注）',
    description: '低对比路网，适合作为全站背景',
  },
  {
    id: 'carto_dark',
    label: 'Carto 暗色（含标注）',
    description: '暗色底图 + 地名/路名，适合交互地图面板',
  },
  {
    id: 'osm',
    label: 'OSM 标准',
    description: 'OpenStreetMap 官方亮色瓦片',
  },
]

export const DEFAULT_PANEL_BASEMAP: BasemapVariant = 'osm'
export const DEFAULT_APP_BASEMAP: BasemapVariant = 'osm'

const CARTO_ATTRIBUTION = '© OpenStreetMap contributors © CARTO'
const TIANDITU_ATTRIBUTION = '© 天地图 · 国家地理信息公共服务平台'

/** 浏览器端天地图 token（前端直连官方多子域，不经后端代理） */
export const TIANDITU_BROWSER_TOKEN = (import.meta.env.VITE_TIANDITU_TOKEN ?? '').trim()

/** 天地图官方 8 个子域，直连多子域可绕开同源 6 连接上限，显著提速 */
const TIANDITU_SUBDOMAINS = ['0', '1', '2', '3', '4', '5', '6', '7']

/**
 * 天地图 WMTS KVP 瓦片地址（浏览器直连官方多子域 t0-t7）。
 * 与 CesiumMap 使用的 tileMatrixSet=w（EPSG:3857）一致，z/x/y 可直接映射。
 * token 为浏览器端 key，随请求以 tk 参数携带。
 */
function createTiandituSource(layer: 'img' | 'cia') {
  return new XYZ({
    attributions: TIANDITU_ATTRIBUTION,
    crossOrigin: 'anonymous',
    tileUrlFunction: (tileCoord: TileCoord) => {
      const [z, x, y] = tileCoord
      const sub = TIANDITU_SUBDOMAINS[(x + y) % TIANDITU_SUBDOMAINS.length]
      const params = new URLSearchParams({
        SERVICE: 'WMTS',
        REQUEST: 'GetTile',
        VERSION: '1.0.0',
        LAYER: layer,
        STYLE: 'default',
        FORMAT: 'tiles',
        TILEMATRIXSET: 'w',
        TILEMATRIX: String(z),
        TILEROW: String(y),
        TILECOL: String(x),
        tk: TIANDITU_BROWSER_TOKEN,
      })
      return `https://t${sub}.tianditu.gov.cn/${layer}_w/wmts?${params.toString()}`
    },
  })
}

function createCartoSource(path: 'dark_all' | 'dark_nolabels') {
  return new XYZ({
    url: `https://{a-d}.basemaps.cartocdn.com/${path}/{z}/{x}/{y}{r}.png`,
    attributions: CARTO_ATTRIBUTION,
  })
}

function createTiandituLayer(): LayerGroup {
  return new LayerGroup({
    layers: [
      new TileLayer({ source: createTiandituSource('img') }),
      new TileLayer({ source: createTiandituSource('cia') }),
    ],
  })
}

export function createBasemapLayer(variant: BasemapVariant): BaseLayer {
  switch (variant) {
    case 'tianditu':
      return createTiandituLayer()
    case 'carto_dark':
      return new TileLayer({ source: createCartoSource('dark_all') })
    case 'carto_dark_nolabels':
      return new TileLayer({ source: createCartoSource('dark_nolabels') })
    case 'osm':
    default:
      return new TileLayer({ source: new OSM() })
  }
}
