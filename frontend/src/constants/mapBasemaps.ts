import TileLayer from 'ol/layer/Tile'
import OSM from 'ol/source/OSM'
import XYZ from 'ol/source/XYZ'

export type BasemapVariant = 'osm' | 'carto_dark' | 'carto_dark_nolabels'

export interface BasemapOption {
  id: BasemapVariant
  label: string
  description: string
}

/** 可选底图，默认 carto_dark_nolabels 适合全站背景 */
export const BASEMAP_OPTIONS: BasemapOption[] = [
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

function createCartoSource(path: 'dark_all' | 'dark_nolabels') {
  return new XYZ({
    url: `https://{a-d}.basemaps.cartocdn.com/${path}/{z}/{x}/{y}{r}.png`,
    attributions: CARTO_ATTRIBUTION,
  })
}

export function createBasemapLayer(variant: BasemapVariant): TileLayer {
  switch (variant) {
    case 'carto_dark':
      return new TileLayer({ source: createCartoSource('dark_all') })
    case 'carto_dark_nolabels':
      return new TileLayer({ source: createCartoSource('dark_nolabels') })
    case 'osm':
    default:
      return new TileLayer({ source: new OSM() })
  }
}
