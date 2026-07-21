import * as mapvthree from '@baidumap/mapv-three'
import type { MapGeoJsonResponse } from '../types/map'
import {
  buildDetailedRoadData,
  type DetailedRoadData,
  type RoadCoordinateProjector,
} from './roadGeometry'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'

type RoadLayerKey = keyof DetailedRoadData

export class BaiduDetailedRoadRenderer {
  private readonly engine: mapvthree.Engine
  private readonly shoulder: mapvthree.Polygon
  private readonly mainSurface: mapvthree.Polygon
  private readonly secondarySurface: mapvthree.Polygon
  private readonly junctionSurface: mapvthree.Polygon
  private readonly outerBoundary: mapvthree.Polyline
  private readonly median: mapvthree.Polyline
  private readonly laneDivider: mapvthree.Polyline
  private readonly stopLine: mapvthree.Polygon
  private readonly crosswalk: mapvthree.Polygon
  private readonly projector: RoadCoordinateProjector
  private currentKey: string | null = null

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.engine = engine
    this.projector = projector
    this.shoulder = this.addPolygon('#27334a', 0.98, 10)
    this.mainSurface = this.addPolygon('#53627e', 0.98, 11)
    this.secondarySurface = this.addPolygon('#3c4962', 0.98, 11)
    this.junctionSurface = this.addPolygon('#53627e', 0.98, 12)
    this.outerBoundary = engine.add(new mapvthree.Polyline({
      flat: true,
      color: '#f4f7fb',
      lineWidth: 2,
      opacity: 0.92,
      height: 0.2,
    }))
    this.median = engine.add(new mapvthree.Polyline({
      flat: true,
      color: '#f4c44d',
      lineWidth: 2,
      opacity: 0.98,
      height: 0.22,
    }))
    this.laneDivider = engine.add(new mapvthree.Polyline({
      flat: true,
      color: '#f4f7fb',
      lineWidth: 1.5,
      dashed: true,
      dashArray: 8,
      dashRatio: 0.45,
      opacity: 0.92,
      height: 0.24,
    }))
    this.outerBoundary.position.z = 1
    this.median.position.z = 1.02
    this.laneDivider.position.z = 1.04
    this.stopLine = this.addPolygon('#f5f7fb', 0.98, 18)
    this.crosswalk = this.addPolygon('#f5f7fb', 0.96, 19)
  }

  private addPolygon(color: string, opacity: number, renderOrder: number): mapvthree.Polygon {
    const polygon = this.engine.add(new mapvthree.Polygon({
      color,
      opacity,
      zOffset: 1,
    }))
    polygon.perPositionHeight = true
    polygon.renderOrder = renderOrder
    return polygon
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

    const data = buildDetailedRoadData(response, this.projector)
    const sources = {
      shoulders: this.source(data, 'shoulders'),
      mainSurfaces: this.source(data, 'mainSurfaces'),
      secondarySurfaces: this.source(data, 'secondarySurfaces'),
      junctionSurfaces: this.source(data, 'junctionSurfaces'),
      outerBoundaries: this.source(data, 'outerBoundaries'),
      medians: this.source(data, 'medians'),
      laneDividers: this.source(data, 'laneDividers'),
      stopLines: this.source(data, 'stopLines'),
      crosswalkStripes: this.source(data, 'crosswalkStripes'),
    }

    this.shoulder.dataSource = sources.shoulders
    this.mainSurface.dataSource = sources.mainSurfaces
    this.secondarySurface.dataSource = sources.secondarySurfaces
    this.junctionSurface.dataSource = sources.junctionSurfaces
    this.outerBoundary.dataSource = sources.outerBoundaries
    this.median.dataSource = sources.medians
    this.laneDivider.dataSource = sources.laneDividers
    this.stopLine.dataSource = sources.stopLines
    this.crosswalk.dataSource = sources.crosswalkStripes
    this.currentKey = nextKey
    this.engine.requestRender()
  }

  private source(data: DetailedRoadData, key: RoadLayerKey): mapvthree.GeoJSONDataSource | null {
    if (data[key].length === 0) return null
    return mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: data[key] })
  }

  clear(): void {
    this.currentKey = null
    for (const layer of this.layers()) layer.dataSource?.clear()
    this.engine.requestRender()
  }

  destroy(): void {
    this.clear()
    for (const layer of this.layers()) this.engine.remove(layer)
  }

  private layers(): Array<mapvthree.Polygon | mapvthree.Polyline> {
    return [
      this.shoulder,
      this.mainSurface,
      this.secondarySurface,
      this.junctionSurface,
      this.outerBoundary,
      this.median,
      this.laneDivider,
      this.stopLine,
      this.crosswalk,
    ]
  }
}
