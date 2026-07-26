import * as mapvthree from '@baidumap/mapv-three'
import { Color } from 'three'
import type { RoadCoordinateProjector } from '../roadGeometry'
import { projectFeatureCollection } from './showcaseLayerData'
import { SHOWCASE_CITY_THEME } from './showcaseCityTheme'

export interface ShowcaseGeoJsonLayerUrls {
  water?: string
  green?: string
  urban?: string
  buildings?: string
  labels?: string
}

export class ShowcaseGeoJsonLayers {
  private readonly engine: mapvthree.Engine
  private readonly projector: RoadCoordinateProjector
  private readonly layers: Array<mapvthree.Polygon | mapvthree.Text> = []
  private waterMaterial: mapvthree.WaterMaterial | null = null
  private destroyed = false

  constructor(engine: mapvthree.Engine, projector: RoadCoordinateProjector) {
    this.engine = engine
    this.projector = projector
  }

  async load(urls: ShowcaseGeoJsonLayerUrls): Promise<void> {
    await Promise.all([
      this.loadSafely('water', urls.water),
      this.loadSafely('green', urls.green),
      this.loadSafely('urban', urls.urban),
      this.loadSafely('buildings', urls.buildings),
      this.loadSafely('labels', urls.labels),
    ])
    this.engine.requestRender()
  }

  private async loadSafely(
    kind: keyof ShowcaseGeoJsonLayerUrls,
    url: string | undefined,
  ): Promise<void> {
    if (!url) return
    try {
      const data = await this.fetchProjected(url)
      if (this.destroyed) return
      if (kind === 'water') this.addWater(data)
      else if (kind === 'green') this.addGreen(data)
      else if (kind === 'urban') this.addUrban(data)
      else if (kind === 'buildings') this.addBuildings(data)
      else this.addLabels(data)
    } catch (cause) {
      console.warn(`[showcase-layers] ${kind} layer disabled`, cause)
    }
  }

  private async fetchProjected(url: string) {
    const response = await fetch(url)
    if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`)
    return projectFeatureCollection(await response.json(), this.projector)
  }

  private addWater(data: object): void {
    const layer = this.engine.add(new mapvthree.Polygon({ zOffset: 0.04 }))
    const material = new mapvthree.WaterMaterial({})
    material.waterColor = new Color(SHOWCASE_CITY_THEME.water)
    material.sunColor = new Color(SHOWCASE_CITY_THEME.water)
    material.reflectionColor = new Color(SHOWCASE_CITY_THEME.water)
    material.timeScaleFactor = 0.002
    layer.material = material
    layer.renderOrder = 2
    layer.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(data)
    this.engine.addBeforeRenderObject(material)
    this.waterMaterial = material
    this.layers.push(layer)
  }

  private addGreen(data: object): void {
    const layer = this.engine.add(new mapvthree.Polygon({
      color: SHOWCASE_CITY_THEME.green,
      zOffset: 0.03,
    }))
    layer.renderOrder = 1
    layer.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(data)
    this.layers.push(layer)
  }

  private addUrban(data: object): void {
    const layer = this.engine.add(new mapvthree.Polygon({
      color: SHOWCASE_CITY_THEME.urban,
      zOffset: 0.02,
    }))
    layer.renderOrder = 0
    layer.dataSource = mapvthree.GeoJSONDataSource.fromGeoJSON(data)
    this.layers.push(layer)
  }

  private addBuildings(data: object): void {
    const layer = this.engine.add(new mapvthree.Polygon({
      color: SHOWCASE_CITY_THEME.building,
      zOffset: 0.08,
      vertexHeights: true,
      extrude: true,
    }))
    const source = mapvthree.GeoJSONDataSource.fromGeoJSON(data)
    source.defineAttribute('height', 'height')
    layer.renderOrder = 4
    layer.dataSource = source
    this.layers.push(layer)
  }

  private addLabels(data: object): void {
    const layer = this.engine.add(new mapvthree.Text({
      fillStyle: SHOWCASE_CITY_THEME.label,
      fontSize: 13,
      flat: false,
    }))
    const source = mapvthree.GeoJSONDataSource.fromGeoJSON(data)
    source.defineAttribute('text', (properties) => String(properties.name ?? properties.Name ?? ''))
    layer.renderOrder = 30
    layer.dataSource = source
    this.layers.push(layer)
  }

  destroy(): void {
    this.destroyed = true
    for (const layer of this.layers) {
      layer.dataSource?.clear()
      this.engine.remove(layer)
    }
    this.layers.length = 0
    if (this.waterMaterial) {
      this.engine.removeBeforeRenderObject(this.waterMaterial)
      this.waterMaterial.dispose()
      this.waterMaterial = null
    }
  }
}
