import type { CesiumCameraPresetId } from '../types/map'

export interface IntersectionFocusOptions {
  force?: boolean
  duration?: number
  complete?: () => void
  cameraPreset?: Exclude<CesiumCameraPresetId, 'overview'>
}

export interface IntersectionFocusTransaction {
  anchorId: string
  cameraPreset: Exclude<CesiumCameraPresetId, 'overview'>
  viewport: {
    kind: 'center'
    center: [number, number]
    zoom: number
  }
  applyOptions: {
    force?: boolean
    duration: number
    complete?: () => void
  }
}

export function createIntersectionFocusTransaction(
  center: [number, number],
  intersectionId: string,
  options: IntersectionFocusOptions = {},
): IntersectionFocusTransaction {
  return {
    anchorId: `intersection:${intersectionId}`,
    cameraPreset: options.cameraPreset ?? 'intersection',
    viewport: {
      kind: 'center',
      center: [...center],
      zoom: 19,
    },
    applyOptions: {
      force: options.force,
      duration: options.duration ?? 900,
      complete: options.complete,
    },
  }
}
