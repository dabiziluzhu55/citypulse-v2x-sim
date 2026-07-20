declare module '@baidumap/mapv-three' {
  export class Engine {
    constructor(container: HTMLElement, options?: Record<string, unknown>)
    map: {
      flyTo: (target: unknown, options: unknown) => void
      setViewport: (points: unknown, options: unknown) => void
      getCenter: () => number[]
      getRange: () => number
      getHeading: () => number
      getPitch: () => number
    }
    controller: {
      enabled: boolean
      enableRotate: boolean
      enableZoom: boolean
      enablePan: boolean
      enableTilt: boolean
    }
    add<T>(object: T): T
    remove(object: unknown): void
    requestRender(): void
    dispose(): void
  }

  export class Polyline {
    constructor(options?: Record<string, unknown>)
    lineWidth: number
    dataSource: GeoJSONDataSource | null
  }

  export class Polygon {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
  }

  export class Circle {
    constructor(options?: Record<string, unknown>)
    dataSource: GeoJSONDataSource | null
  }

  export class GeoJSONDataSource {
    static fromGeoJSON(data: object | object[]): GeoJSONDataSource
    clear(): void
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
    releaseCameraViewport: () => void
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

  export class BaiduMapConfig {
    static ak: string
  }

  export const PROJECTION_WEB_MERCATOR: string
}
