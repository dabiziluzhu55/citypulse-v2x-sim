/** 地图蒙版与 flyTo 动画参数 */

export const MAP_FLY_DURATION_MS = 800

/** fitBounds padding: [top, right, bottom, left] — 左右留侧栏空间 */
export const MAP_FIT_PADDING: [number, number, number, number] = [80, 480, 80, 440]

export type MapMaskVariant = 'dashboard' | 'content'

/** 首页三栏布局：左右渐暗、中间透亮 */
export const DASHBOARD_MASK_GRADIENT = `linear-gradient(
  90deg,
  rgba(2, 5, 10, 0.88) 0%,
  rgba(2, 5, 10, 0.72) 14%,
  rgba(2, 5, 10, 0.45) 22%,
  rgba(2, 5, 10, 0.12) 36%,
  rgba(2, 5, 10, 0.04) 50%,
  rgba(2, 5, 10, 0.12) 64%,
  rgba(2, 5, 10, 0.45) 78%,
  rgba(2, 5, 10, 0.72) 86%,
  rgba(2, 5, 10, 0.88) 100%
)`

/** 内容页：全宽略暗 + 弱水平 vignette */
export const CONTENT_MASK_GRADIENT = `${DASHBOARD_MASK_GRADIENT},
  linear-gradient(180deg, rgba(2, 5, 10, 0.42) 0%, transparent 14%, transparent 86%, rgba(2, 5, 10, 0.32) 100%)`

export const DASHBOARD_MASK_VERTICAL = `linear-gradient(
  180deg,
  rgba(2, 5, 10, 0.35) 0%,
  transparent 12%,
  transparent 88%,
  rgba(2, 5, 10, 0.25) 100%
)`

/** 窄屏单列：均匀蒙版 */
export const MOBILE_MASK_GRADIENT = `linear-gradient(
  180deg,
  rgba(2, 5, 10, 0.78) 0%,
  rgba(2, 5, 10, 0.52) 18%,
  rgba(2, 5, 10, 0.38) 50%,
  rgba(2, 5, 10, 0.52) 82%,
  rgba(2, 5, 10, 0.78) 100%
)`
