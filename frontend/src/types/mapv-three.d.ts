declare module '@baidumap/mapv-three' {
  export class Engine {
    constructor(container: HTMLElement, options?: Record<string, unknown>)
    map: {
      flyTo: (target: unknown, options: unknown) => void
      setViewport: (points: unknown, options: unknown) => void
      setBounds: (bounds: number[][]) => void
      setMaxRange: (range: number) => void
      setMinRange: (range: number) => void
      getCenter: () => number[]
      getRange: () => number
      getHeading: () => number
      getPitch: () => number
      projectArrayCoordinate: (coordinate: readonly number[]) => number[]
    }
    controller: {
      enabled: boolean
      enableRotate: boolean
      enableZoom: boolean
      enablePan: boolean
      enableTilt: boolean
    }
    rendering: {
      enableAnimationLoop: boolean
      animationLoopFrameTime: number
    }
    add<T>(object: T): T
    remove(object: unknown): void
    addBeforeRenderObject(object: unknown): void
    removeBeforeRenderObject(object: unknown): void
    requestRender(): void
    dispose(): void
  }

  export class Polyline {
    constructor(options?: Record<string, unknown>)
    lineWidth: number
    dataSource: GeoJSONDataSource | null
    position: { z: number }
    material: import('three').Material
    renderOrder: number
  }

  export class Polygon {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
    material: unknown
    position: { z: number }
    perPositionHeight: boolean
    renderOrder: number
    extrude: boolean
    extrudeValue: number
  }

  export class Circle {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
  }

  export class GeoJSONDataSource {
    static fromGeoJSON(data: object | object[]): GeoJSONDataSource
    defineAttribute(
      name: string,
      value?: string | ((properties: Record<string, unknown>) => unknown),
    ): GeoJSONDataSource
    clear(): void
  }

  export class Text {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
    renderOrder: number
  }

  export class EffectModelPoint {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
    model: import('three').Object3D | null
    position: { z: number }
  }

  export class EffectPoint {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
    material: import('three').Material & { keepSize?: boolean }
    position: { z: number }
    renderOrder: number
  }

  export class WaterMaterial {
    constructor(options?: Record<string, unknown>)
    waterColor: import('three').Color
    sunColor: import('three').Color
    reflectionColor: import('three').Color
    timeScaleFactor: number
    dispose(): void
  }

  export class Twin {
    constructor(options?: Record<string, unknown>)
    push(data: Array<Record<string, unknown>>): void
    reset(): void
  }

  export const twinConstants: {
    REALISTIC_TEMPLATE_MODEL: {
      CAR: string
      BUS: string
      TRUCK: string
    }
  }

  export class Default3DTiles {
    constructor(options: Record<string, unknown>)
    visible: boolean
    position: {
      x: number
      y: number
      z: number
      set: (x: number, y: number, z: number) => void
    }
    releaseCameraViewport: () => void
    errorTarget: number
    cullRequestsWhileMoving: boolean
    transformFromEcefToPlane: (longitude: number, latitude: number, height?: number) => void
    getBounds: () => {
      min: { x: number; y: number; z: number }
      max: { x: number; y: number; z: number }
    }
    statistics: {
      numberOfPendingRequests: number
      numberOfTilesProcessing: number
      numberOfTilesWithContentReady: number
      numberOfTilesTotal: number
      numberOfLoadedTilesTotal: number
    }
  }

  export class BaiduVectorTileProvider {
    constructor(options: Record<string, unknown>)
  }

  export class DefaultSky {
    color: import('three').Color
    highColor: import('three').Color
    skyLightIntensity: number
  }

  export class BaiduMapConfig {
    static ak: string
  }

  export const PROJECTION_WEB_MERCATOR: string
}
