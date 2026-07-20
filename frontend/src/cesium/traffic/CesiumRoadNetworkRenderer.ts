import * as Cesium from 'cesium'
import type { GeoJsonFeature, MapGeoJsonResponse } from '../../types/map'
import {
  ROAD_CENTERLINE_CSS,
  ROAD_CENTERLINE_WIDTH_PX,
  ROAD_DEFAULT_LANE_COUNT,
  ROAD_EDGE_GLOW_CSS,
  ROAD_EDGE_WIDTH_PX,
  ROAD_JUNCTION_CORE_CSS,
  ROAD_JUNCTION_GLOW_CSS,
  ROAD_JUNCTION_RADIUS_METERS,
  ROAD_LANE_WIDTH_METERS,
  ROAD_MAX_WIDTH_METERS,
  ROAD_MIN_WIDTH_METERS,
  ROAD_SURFACE_CSS,
} from '../../constants/cesiumTrafficVisualization'

const SURFACE_COLOR = Cesium.Color.fromCssColorString(ROAD_SURFACE_CSS)
const EDGE_GLOW_COLOR = Cesium.Color.fromCssColorString(ROAD_EDGE_GLOW_CSS)
const CENTERLINE_COLOR = Cesium.Color.fromCssColorString(ROAD_CENTERLINE_CSS)
const JUNCTION_GLOW_COLOR = Cesium.Color.fromCssColorString(ROAD_JUNCTION_GLOW_CSS)
const JUNCTION_CORE_COLOR = Cesium.Color.fromCssColorString(ROAD_JUNCTION_CORE_CSS)

/** 路面真实宽度（米）= 车道数 × 单车道宽，并做上下限约束 */
function resolveRoadWidthMeters(properties: Record<string, unknown>): number {
  const laneCount = Number(properties.lane_count ?? ROAD_DEFAULT_LANE_COUNT) || ROAD_DEFAULT_LANE_COUNT
  const width = laneCount * ROAD_LANE_WIDTH_METERS
  return Math.min(ROAD_MAX_WIDTH_METERS, Math.max(ROAD_MIN_WIDTH_METERS, width))
}

/**
 * 三维路网渲染器（增强版）。
 * 每条道路分三层渲染：
 *   层1 CorridorGraphics —— 真实米制宽度的沥青路面（贴地）
 *   层2 PolylineGraphics —— 亮青发光路缘描边
 *   层3 PolylineGraphics(Dash) —— 车道中心虚线
 * 路口节点用发光圆柱 + 核心点强化（层5）。
 */
export class CesiumRoadNetworkRenderer {
  private readonly viewer: Cesium.Viewer
  private readonly dataSource: Cesium.CustomDataSource
  private currentKey: string | null = null

  constructor(viewer: Cesium.Viewer) {
    this.viewer = viewer
    this.dataSource = new Cesium.CustomDataSource('citypulse-road-network')
    void this.viewer.dataSources.add(this.dataSource)
  }

  render(response: MapGeoJsonResponse | null): void {
    if (!response) {
      this.clear()
      return
    }
    if (response.intersection_id === this.currentKey) {
      return
    }
    this.currentKey = response.intersection_id
    this.dataSource.entities.removeAll()

    const features = response.geojson?.features ?? []
    for (const feature of features) {
      this.addFeature(feature)
    }
    this.viewer.scene.requestRender()
  }

  private addFeature(feature: GeoJsonFeature): void {
    const geometry = feature.geometry
    if (!geometry) return

    const properties = feature.properties ?? {}
    if (geometry.type === 'LineString') {
      this.addRoad(geometry.coordinates as number[][], properties)
    } else if (geometry.type === 'MultiLineString') {
      for (const line of geometry.coordinates as number[][][]) {
        this.addRoad(line, properties)
      }
    } else if (geometry.type === 'Point') {
      this.addJunction(geometry.coordinates as number[])
    }
  }

  private toPositions(coordinates: number[][]): Cesium.Cartesian3[] {
    const positions: Cesium.Cartesian3[] = []
    for (const coord of coordinates) {
      if (!Array.isArray(coord) || coord.length < 2) continue
      const [lon, lat] = coord
      if (typeof lon !== 'number' || typeof lat !== 'number') continue
      positions.push(Cesium.Cartesian3.fromDegrees(lon, lat))
    }
    return positions
  }

  private addRoad(coordinates: number[][], properties: Record<string, unknown>): void {
    const positions = this.toPositions(coordinates)
    if (positions.length < 2) return

    const widthMeters = resolveRoadWidthMeters(properties)

    // 层1：真实宽度沥青路面（贴地走廊面）
    this.dataSource.entities.add({
      corridor: {
        positions,
        width: widthMeters,
        cornerType: Cesium.CornerType.ROUNDED,
        classificationType: Cesium.ClassificationType.TERRAIN,
        material: SURFACE_COLOR,
      },
    })

    // 层2：亮青发光路缘描边（贴地细线，勾出路面轮廓）
    this.dataSource.entities.add({
      polyline: {
        positions,
        width: ROAD_EDGE_WIDTH_PX,
        clampToGround: true,
        material: new Cesium.PolylineGlowMaterialProperty({
          color: EDGE_GLOW_COLOR,
          glowPower: 0.25,
        }),
      },
    })

    // 层3：车道中心虚线
    this.dataSource.entities.add({
      polyline: {
        positions,
        width: ROAD_CENTERLINE_WIDTH_PX,
        clampToGround: true,
        material: new Cesium.PolylineDashMaterialProperty({
          color: CENTERLINE_COLOR,
          dashLength: 16,
        }),
      },
    })
  }

  private addJunction(coordinate: number[]): void {
    if (!Array.isArray(coordinate) || coordinate.length < 2) return
    const [lon, lat] = coordinate
    if (typeof lon !== 'number' || typeof lat !== 'number') return

    const position = Cesium.Cartesian3.fromDegrees(lon, lat)

    // 层5：路口发光圆环（贴地椭圆环）+ 核心亮点
    this.dataSource.entities.add({
      position,
      ellipse: {
        semiMajorAxis: ROAD_JUNCTION_RADIUS_METERS,
        semiMinorAxis: ROAD_JUNCTION_RADIUS_METERS,
        height: 0,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        material: JUNCTION_GLOW_COLOR.withAlpha(0.28),
        outline: true,
        outlineColor: JUNCTION_GLOW_COLOR,
        outlineWidth: 2,
        classificationType: Cesium.ClassificationType.TERRAIN,
      },
    })

    this.dataSource.entities.add({
      position,
      point: {
        pixelSize: 8,
        color: JUNCTION_CORE_COLOR,
        outlineColor: JUNCTION_GLOW_COLOR,
        outlineWidth: 2,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    })
  }

  clear(): void {
    this.currentKey = null
    this.dataSource.entities.removeAll()
  }

  destroy(): void {
    this.dataSource.entities.removeAll()
    this.viewer.dataSources.remove(this.dataSource, true)
  }
}
