export const MAP3D_PRESENTATION_TIMEOUT_MS = 25_000
export const BUILDING_STABLE_SAMPLE_COUNT = 3
export const BUILDING_STABLE_SAMPLE_INTERVAL_MS = 250
export const FINAL_RENDER_FRAME_COUNT = 2

export interface TilesetReadinessSample {
  hasContent: boolean
  pendingRequests: number
  processingTiles: number
}

export interface Map3dPresentationSignals {
  providerReady: boolean
  cameraReady: boolean
  overviewReady: boolean
  intersectionReady: boolean
  environmentReady: boolean
  buildingRequired: boolean
  buildingStableSamples: number
}

export function advanceStableTileSamples(
  previous: number,
  sample: TilesetReadinessSample,
): number {
  return sample.hasContent && sample.pendingRequests === 0 && sample.processingTiles === 0
    ? previous + 1
    : 0
}

export function map3dPresentationReady(signals: Map3dPresentationSignals): boolean {
  return signals.providerReady
    && signals.cameraReady
    && signals.overviewReady
    && signals.intersectionReady
    && signals.environmentReady
    && (!signals.buildingRequired
      || signals.buildingStableSamples >= BUILDING_STABLE_SAMPLE_COUNT)
}

export function map3dLoadingStage(signals: Map3dPresentationSignals): string {
  if (!signals.providerReady) return '正在初始化百度底图'
  if (!signals.cameraReady) return '正在定位最终三维视角'
  if (!signals.overviewReady) return '正在加载 20 路口道路总览'
  if (!signals.intersectionReady) return '正在加载当前高精度路口'
  if (!signals.environmentReady) return '正在加载路灯与路口设施'
  if (signals.buildingRequired && signals.buildingStableSamples < BUILDING_STABLE_SAMPLE_COUNT) {
    return '正在加载当前视野内的本地建筑'
  }
  return '正在完成三维场景渲染'
}
