export interface Map3dCapabilityInput {
  webgl: boolean
  webgl2: boolean
  hardwareConcurrency: number
  deviceMemory?: number
}

export interface Map3dCapability {
  supported: boolean
  quality: 'full' | 'reduced' | 'unsupported'
  reason: string | null
}

export function resolveMap3dCapability(input: Map3dCapabilityInput): Map3dCapability {
  if (!input.webgl) {
    return { supported: false, quality: 'unsupported', reason: '当前浏览器或显卡不支持 WebGL' }
  }
  const constrained = !input.webgl2
    || input.hardwareConcurrency < 4
    || (input.deviceMemory !== undefined && input.deviceMemory < 4)
  return {
    supported: true,
    quality: constrained ? 'reduced' : 'full',
    reason: constrained ? '当前设备使用精简三维质量' : null,
  }
}

export function detectMap3dCapability(): Map3dCapability {
  if (typeof document === 'undefined') {
    return { supported: false, quality: 'unsupported', reason: '三维地图需要浏览器环境' }
  }
  const canvas = document.createElement('canvas')
  const webgl2 = Boolean(canvas.getContext('webgl2', { failIfMajorPerformanceCaveat: true }))
  const webgl = webgl2 || Boolean(
    canvas.getContext('webgl', { failIfMajorPerformanceCaveat: true })
      ?? canvas.getContext('experimental-webgl'),
  )
  const navigatorWithMemory = navigator as Navigator & { deviceMemory?: number }
  return resolveMap3dCapability({
    webgl,
    webgl2,
    hardwareConcurrency: navigator.hardwareConcurrency || 2,
    deviceMemory: navigatorWithMemory.deviceMemory,
  })
}
