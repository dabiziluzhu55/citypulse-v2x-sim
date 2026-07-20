import * as mapvthree from '@baidumap/mapv-three'
import type { MapGeoJsonResponse } from '../types/map'
import { buildDetailedRoadData, type DetailedRoadData } from './roadGeometry'

const EMPTY_COLLECTION = { type: 'FeatureCollection', features: [] }

export class BaiduDetailedRoadRenderer {
  private readonly engine: mapvthree.Engine
  private readonly surface: mapvthree.Polygon
  private readonly boundary: mapvthree.Polyline
  private readonly divider: mapvthree.Polyline
  private currentKey: string | null = null

  constructor(engine: mapvthree.Engine) {
    this.engine = engine
    this.surface = engine.add(new mapvthree.Polygon({
      color: '#1c2b3a',
      opacity: 0.9,
      perPositionHeight: true,
      zOffset: 0.08,
      renderOrder: -10,
    }))
    this.boundary = engine.add(new mapvthree.Polyline({
      flat: true,
      color: '#9db1bf',
      lineWidth: 0.16,
      opacity: 0.62,
      height: 0.28,
    }))
    this.divider = engine.add(new mapvthree.Polyline({
      flat: true,
      color: '#d9e6e8',
      lineWidth: 0.13,
      dashed: true,
      dashArray: 9,
      dashRatio: 0.42,
      opacity: 0.74,
      height: 0.34,
    }))
  }

  render(response: MapGeoJsonResponse | null): void {
    if (!response) {
      this.clear()
      return
    }
    const metadata = response.geojson?.metadata ?? {}
    const dataVersion = String(metadata.generated_at ?? metadata.data_version ?? metadata.vertex_count ?? '')
    const nextKey = `${response.intersection_id}:${response.radius_m}:${dataVersion}`
    if (nextKey === this.currentKey) return
    this.currentKey = nextKey

    const data = buildDetailedRoadData(response)
    this.surface.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({
      type: 'FeatureCollection',
      features: data.surfaces,
    })
    this.boundary.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(this.asCollection(data, 'boundaries'))
    this.divider.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(this.asCollection(data, 'dividers'))
    this.engine.requestRender()
  }

  private asCollection(data: DetailedRoadData, key: 'boundaries' | 'dividers'): object {
    return { type: 'FeatureCollection', features: data[key] }
  }

  clear(): void {
    this.currentKey = null
    this.surface.dataSource?.clear()
    this.boundary.dataSource?.clear()
    this.divider.dataSource?.clear()
    this.engine.requestRender()
  }

  destroy(): void {
    this.clear()
    this.engine.remove(this.surface)
    this.engine.remove(this.boundary)
    this.engine.remove(this.divider)
  }
}

export { EMPTY_COLLECTION }
