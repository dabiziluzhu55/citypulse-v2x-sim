import type { AppMapView } from '../types/map.ts'

export function captureMapView(
  view: Pick<AppMapView, 'mode' | 'cameraPreset' | 'anchorId' | 'viewport'>,
  apply: () => void,
): () => void {
  const saved = {
    mode: view.mode.value,
    cameraPreset: view.cameraPreset.value,
    anchorId: view.anchorId.value,
    viewport: JSON.parse(JSON.stringify(view.viewport.value)),
  }
  return () => {
    view.mode.value = saved.mode
    view.cameraPreset.value = saved.cameraPreset
    view.anchorId.value = saved.anchorId
    view.viewport.value = JSON.parse(JSON.stringify(saved.viewport))
    apply()
  }
}
