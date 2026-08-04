import * as mapvthree from '@baidumap/mapv-three'
import { Material, Mesh, Object3D, Texture } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { MapGeoJsonResponse } from '../../types/map'
import { buildDetailedRoadData, type RoadCoordinateProjector } from '../roadGeometry'
import {
  junctionSurfacesToPoints,
  MAX_SHOWCASE_MODEL_INSTANCES,
} from './showcaseLayerData'

export interface ShowcaseLandmark {
  url: string
  position: [number, number, number]
  rotation?: [number, number, number]
  scale?: number
}

export class ShowcaseModelLayers {
  private readonly engine: mapvthree.Engine
  private readonly projector: RoadCoordinateProjector
  private readonly junctionMarkers: mapvthree.EffectModelPoint
  private landmark: Object3D | null = null
  private landmarkKey: string | null = null
  private landmarkRequestVersion = 0
  private currentKey: string | null = null
  private destroyed = false

  constructor(engine: mapvthree.Engine, projector: RoadCoordinateProjector) {
    this.engine = engine
    this.projector = projector
    this.junctionMarkers = engine.add(new mapvthree.EffectModelPoint({
      animationRotate: false,
      height: 0.8,
      keepSize: false,
      size: 2.5,
    }))
  }

  render(response: MapGeoJsonResponse | null): void {
    if (!response) {
      this.clear()
      return
    }
    const metadata = response.geojson.metadata ?? {}
    const version = String(metadata.generated_at ?? metadata.data_version ?? metadata.vertex_count ?? '')
    const nextKey = `${response.intersection_id}:${response.radius_m}:${version}`
    if (nextKey === this.currentKey) return

    const detailed = buildDetailedRoadData(response, this.projector)
    if (detailed.junctionSurfaces.length > MAX_SHOWCASE_MODEL_INSTANCES) {
      console.warn(`[showcase-layers] junction markers capped at ${MAX_SHOWCASE_MODEL_INSTANCES}`)
    }
    const features = junctionSurfacesToPoints(detailed.junctionSurfaces)
    this.junctionMarkers.dataSource = features.length
      ? mapvthree.GeoJSONDataSource.fromGeoJSON({ type: 'FeatureCollection', features })
      : null
    this.currentKey = nextKey
    this.engine.requestRender()
  }

  async loadLandmark(config: ShowcaseLandmark | null): Promise<void> {
    const version = ++this.landmarkRequestVersion
    if (!config?.url) {
      this.removeLandmark()
      return
    }
    const key = JSON.stringify(config)
    if (key === this.landmarkKey) return
    const gltf = await new GLTFLoader().loadAsync(config.url)
    if (this.destroyed || version !== this.landmarkRequestVersion) {
      disposeObject(gltf.scene)
      return
    }
    const projected = this.projector(config.position)
    const geographicPosition = projected[2] == null
      ? [projected[0], projected[1]]
      : [projected[0], projected[1], projected[2]]
    const scenePosition = this.engine.map.projectArrayCoordinate(geographicPosition)
    const model = gltf.scene
    model.position.set(scenePosition[0], scenePosition[1], scenePosition[2] ?? 0)
    model.rotation.set(...(config.rotation ?? [Math.PI / 2, 0, 0]))
    model.scale.setScalar(config.scale ?? 1)
    const previous = this.landmark
    this.landmark = this.engine.add(model)
    this.landmarkKey = key
    if (previous) {
      this.engine.remove(previous)
      disposeObject(previous)
    }
    this.engine.requestRender()
  }

  private removeLandmark(): void {
    this.landmarkKey = null
    if (!this.landmark) return
    this.engine.remove(this.landmark)
    disposeObject(this.landmark)
    this.landmark = null
    this.engine.requestRender()
  }

  clear(): void {
    this.currentKey = null
    this.junctionMarkers.dataSource?.clear()
    this.junctionMarkers.dataSource = null
    this.engine.requestRender()
  }

  destroy(): void {
    this.destroyed = true
    this.landmarkRequestVersion += 1
    this.clear()
    this.engine.remove(this.junctionMarkers)
    this.removeLandmark()
  }
}

function disposeObject(object: Object3D): void {
  object.traverse((child: Object3D) => {
    if (!(child instanceof Mesh)) return
    child.geometry.dispose()
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    for (const material of materials) disposeMaterial(material)
  })
}

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material as unknown as Record<string, unknown>)) {
    if (value instanceof Texture) value.dispose()
  }
  material.dispose()
}
