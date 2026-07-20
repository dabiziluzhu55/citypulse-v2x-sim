/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

interface ImportMetaEnv {
  readonly VITE_BACKEND_PROXY_TARGET?: string
  readonly VITE_API_BASE_URL?: string
  readonly VITE_TRAFFIC_WS_URL?: string
  readonly VITE_DEV_USE_POLLING?: string
  readonly VITE_DEFAULT_RUN_ID?: string
  readonly VITE_DEFAULT_EXPERIMENT_ID?: string
  readonly VITE_XIONGAN_3DTILES_URL?: string
  readonly VITE_ENABLE_XIONGAN_3DTILES?: string
  readonly VITE_TIANDITU_TOKEN?: string
  readonly VITE_BAIDU_MAP_AK?: string
  readonly VITE_AMAP_MAP_KEY?: string
  readonly VITE_BAIDU_MAP_STYLE?: string
  readonly VITE_CESIUM_ION_TOKEN?: string
  readonly VITE_CESIUM_CAR_MODEL_URI?: string
  readonly VITE_CESIUM_TRUCK_MODEL_URI?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare namespace AMap {
  interface MapOptions {
    viewMode?: '2D' | '3D'
    zoom?: number
    pitch?: number
    rotation?: number
    center?: [number, number]
    mapStyle?: string
    showBuildingBlock?: boolean
    buildingAnimation?: boolean
    skyColor?: string
  }

  class Map {
    constructor(container: HTMLElement, options?: MapOptions)
    add(overlay: unknown): void
    on(eventName: string, handler: () => void): void
    destroy(): void
  }

  class Pixel {
    constructor(x: number, y: number)
  }

  class Marker {
    constructor(options: {
      position: [number, number]
      anchor?: string
      content?: string
      offset?: Pixel
    })
    setLabel(options: {
      direction?: string
      offset?: Pixel
      content?: string
    }): void
  }
}

declare global {
  interface Window {
    AMap?: typeof AMap
  }
}
