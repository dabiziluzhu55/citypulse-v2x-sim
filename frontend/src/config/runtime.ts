/** Public browser credentials only. Never add server secrets to this object. */
export interface BrowserRuntimeConfig {
  baiduMapAk?: string
  tiandituToken?: string
  cartoBasemapKey?: string
  amapMapKey?: string
}

declare global {
  interface Window {
    __CITYPULSE_CONFIG__?: BrowserRuntimeConfig
  }
}

export function browserConfigValue(key: keyof BrowserRuntimeConfig, developmentValue?: string): string {
  const value = typeof window === 'undefined' ? undefined : window.__CITYPULSE_CONFIG__?.[key]
  if (value !== undefined) return typeof value === 'string' ? value.trim() : ''
  // Production credentials come exclusively from runtime-config.js.
  return import.meta.env.DEV ? (developmentValue ?? '').trim() : ''
}
