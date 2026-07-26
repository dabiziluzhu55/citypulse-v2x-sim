import * as mapvthree from '@baidumap/mapv-three'
import type { GeoJsonFeature, MapGeoJsonResponse } from '../types/map'
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
} from '../constants/cesiumTrafficVisualization'
import { projectSimulationCoordinateToBaiduMap } from './sceneCoordinates'
import type { RoadCoordinateProjector } from './roadGeometry'

const ROAD_SURFACE_HEIGHT_METERS = 0.25
const ROAD_EDGE_HEIGHT_METERS = 0.35
const ROAD_CENTERLINE_HEIGHT_METERS = 0.45

interface RoadFeature extends Record<string, unknown> {
  type: 'Feature'
  geometry: { type: 'LineString'; coordinates: number[][] }
  properties: Record<string, unknown>
}

function resolveRoadWidthMeters(properties: Record<string, unknown>): number {
  const explicitWidth = Number(properties.width_m)
  if (Number.isFinite(explicitWidth) && explicitWidth > 0) {
    return Math.min(ROAD_MAX_WIDTH_METERS, Math.max(ROAD_MIN_WIDTH_METERS, explicitWidth))
  }
  const laneCount = Number(properties.lane_count ?? ROAD_DEFAULT_LANE_COUNT) || ROAD_DEFAULT_LANE_COUNT
  return Math.min(ROAD_MAX_WIDTH_METERS, Math.max(ROAD_MIN_WIDTH_METERS, laneCount * ROAD_LANE_WIDTH_METERS))
}

function projectCoordinate(
  coordinate: number[],
  height: number,
  projector: RoadCoordinateProjector,
): number[] {
  const projected = projector(coordinate)
  return [projected[0], projected[1], Number(projected[2] ?? 0) + height]
}

function asLineString(coordinates: unknown): number[][] | null {
  if (!Array.isArray(coordinates)) return null
  const line = coordinates.filter(
    (coordinate): coordinate is number[] =>
      Array.isArray(coordinate) && typeof coordinate[0] === 'number' && typeof coordinate[1] === 'number',
  )
  return line.length >= 2 ? line : null
}

function createLineFeature(
  coordinates: number[][],
  properties: Record<string, unknown>,
  height: number,
  projector: RoadCoordinateProjector,
): RoadFeature {
  return {
    type: 'Feature',
    geometry: {
      type: 'LineString',
      coordinates: coordinates.map((coordinate) => projectCoordinate(coordinate, height, projector)),
    },
    properties,
  }
}

export class BaiduRoadNetworkRenderer {
  private readonly engine: mapvthree.Engine
  private readonly surfaces = new Map<number, mapvthree.Polyline>()
  private readonly edge: mapvthree.Polyline
  private readonly centerline: mapvthree.Polyline
  private readonly junctions: mapvthree.Circle
  private readonly projector: RoadCoordinateProjector
  private currentKey: string | null = null

  constructor(
    engine: mapvthree.Engine,
    projector: RoadCoordinateProjector = projectSimulationCoordinateToBaiduMap,
  ) {
    this.engine = engine
    this.projector = projector
    this.edge = engine.add(new mapvthree.Polyline({
      flat: true,
      color: ROAD_EDGE_GLOW_CSS,
      lineWidth: ROAD_EDGE_WIDTH_PX,
      emissive: ROAD_EDGE_GLOW_CSS,
      opacity: 0.9,
    }))
    this.centerline = engine.add(new mapvthree.Polyline({
      flat: true,
      color: ROAD_CENTERLINE_CSS,
      lineWidth: ROAD_CENTERLINE_WIDTH_PX,
      dashed: true,
      dashArray: 16,
      dashRatio: 0.5,
      opacity: 0.9,
    }))
    this.junctions = engine.add(new mapvthree.Circle({
      color: ROAD_JUNCTION_GLOW_CSS,
      size: ROAD_JUNCTION_RADIUS_METERS * 2,
      borderColor: ROAD_JUNCTION_CORE_CSS,
      borderWidth: 2,
      opacity: 0.82,
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

    const surfaceBuckets = new Map<number, RoadFeature[]>()
    const edgeLines: RoadFeature[] = []
    const centerLines: RoadFeature[] = []
    const junctions: object[] = []
    for (const feature of response.geojson?.features ?? []) {
      this.collectFeature(feature, surfaceBuckets, edgeLines, centerLines, junctions)
    }

    this.syncSurfaceLayers(surfaceBuckets)
    this.edge.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: edgeLines })
    this.centerline.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: centerLines })
    this.junctions.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features: junctions })
    this.engine.requestRender()
  }

  private collectFeature(
    feature: GeoJsonFeature,
    surfaceBuckets: Map<number, RoadFeature[]>,
    edgeLines: RoadFeature[],
    centerLines: RoadFeature[],
    junctions: object[],
  ): void {
    const properties = feature.properties ?? {}
    const geometry = feature.geometry
    if (!geometry) return

    const addLine = (coordinates: number[][]) => {
      const width = Math.round(resolveRoadWidthMeters(properties) * 2) / 2
      const bucket = surfaceBuckets.get(width) ?? []
      bucket.push(createLineFeature(coordinates, properties, ROAD_SURFACE_HEIGHT_METERS, this.projector))
      surfaceBuckets.set(width, bucket)
      edgeLines.push(createLineFeature(coordinates, properties, ROAD_EDGE_HEIGHT_METERS, this.projector))
      centerLines.push(createLineFeature(coordinates, properties, ROAD_CENTERLINE_HEIGHT_METERS, this.projector))
    }

    if (geometry.type === 'LineString') {
      const coordinates = asLineString(geometry.coordinates)
      if (coordinates) addLine(coordinates)
      return
    }
    if (geometry.type === 'MultiLineString' && Array.isArray(geometry.coordinates)) {
      for (const candidate of geometry.coordinates) {
        const coordinates = asLineString(candidate)
        if (coordinates) addLine(coordinates)
      }
      return
    }
    if (geometry.type === 'Point' && Array.isArray(geometry.coordinates)) {
      const [longitude, latitude] = geometry.coordinates
      if (typeof longitude === 'number' && typeof latitude === 'number') {
        junctions.push({
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: projectCoordinate(
              [longitude, latitude],
              ROAD_CENTERLINE_HEIGHT_METERS,
              this.projector,
            ),
          },
          properties,
        })
      }
    }
  }

  private syncSurfaceLayers(buckets: Map<number, RoadFeature[]>): void {
    for (const [width, surface] of this.surfaces) {
      if (buckets.has(width)) continue
      surface.dataSource?.clear()
      this.engine.remove(surface)
      this.surfaces.delete(width)
    }
    for (const [width, features] of buckets) {
      let surface = this.surfaces.get(width)
      if (!surface) {
        surface = this.engine.add(new mapvthree.Polyline({
          flat: true,
          color: ROAD_SURFACE_CSS,
          lineWidth: width,
          opacity: 0.82,
        }))
        this.surfaces.set(width, surface)
      }
      surface.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features })
    }
  }

  clear(): void {
    this.currentKey = null
    for (const surface of this.surfaces.values()) surface.dataSource?.clear()
    this.edge.dataSource?.clear()
    this.centerline.dataSource?.clear()
    this.junctions.dataSource?.clear()
    this.engine.requestRender()
  }

  destroy(): void {
    this.clear()
    for (const surface of this.surfaces.values()) this.engine.remove(surface)
    this.surfaces.clear()
    this.engine.remove(this.edge)
    this.engine.remove(this.centerline)
    this.engine.remove(this.junctions)
  }
}
