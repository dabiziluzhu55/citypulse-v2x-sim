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
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
