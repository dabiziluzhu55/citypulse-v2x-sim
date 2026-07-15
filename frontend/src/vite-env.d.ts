/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string

  readonly VITE_WS_URL?: string

  readonly VITE_TRAFFIC_WS_URL?: string

  readonly VITE_DEFAULT_RUN_ID?: string

  readonly VITE_DEFAULT_EXPERIMENT_ID?: string

  readonly VITE_CESIUM_ION_TOKEN?: string

}



interface ImportMeta {

  readonly env: ImportMetaEnv

}

