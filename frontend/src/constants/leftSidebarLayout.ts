/** 左侧数据面板布局常量 */
import { CHROME_SIDEBAR_CENTER_EXPAND } from './dashboardChromeLayout'

/** 原稿外壳宽度（扩展前，用于固定左侧与边框的锚点） */
export const LEFT_SIDEBAR_DESIGN_WIDTH_BASE = 520
export const LEFT_SIDEBAR_DESIGN_WIDTH = LEFT_SIDEBAR_DESIGN_WIDTH_BASE + CHROME_SIDEBAR_CENTER_EXPAND
export const LEFT_SIDEBAR_DESIGN_HEIGHT = 990

/** 内容逻辑画布（原 左侧数据.svg 坐标系） */
export const LEFT_SIDEBAR_CONTENT_WIDTH = 417
export const LEFT_SIDEBAR_CONTENT_HEIGHT = 870

/** 内容等比放大，需配合外壳扩展与裁剪区 */
export const LEFT_SIDEBAR_CONTENT_SCALE = 1.11

/** 裁剪区：贴近外壳内腔，仅保留描边安全距离 */
export const LEFT_SIDEBAR_CLIP_INSET_TOP = 8
export const LEFT_SIDEBAR_CLIP_INSET_LEFT = 13
export const LEFT_SIDEBAR_CLIP_INSET_RIGHT = 2
export const LEFT_SIDEBAR_CLIP_INSET_BOTTOM = 10

/** 区块标题栏（相对 417×870 内容画布） */
export const LEFT_SIDEBAR_SECTION_HEADERS = {
  scenario: {
    left: 12,
    top: 36,
    width: 393,
    height: 45,
    viewBox: '0 0 368 45',
  },
  algorithm: {
    left: 12,
    top: 441,
    width: 393,
    height: 40,
    viewBox: '0 0 354.151 40',
  },
} as const

/** 外壳 SVG 相对内容画布的等比缩放 */
export const LEFT_SIDEBAR_SHELL_SCALE_X =
  LEFT_SIDEBAR_DESIGN_WIDTH / LEFT_SIDEBAR_CONTENT_WIDTH
export const LEFT_SIDEBAR_SHELL_SCALE_Y =
  LEFT_SIDEBAR_DESIGN_HEIGHT / LEFT_SIDEBAR_CONTENT_HEIGHT

/** 外壳装饰（417×870 原稿坐标） */
export const LEFT_SIDEBAR_SHELL = {
  innerScreen: { x: 7.58182, y: 4, width: 408.47, height: 825, rx: 12 },
  progressRail: { y: 760, x1: 12, x2: 405, fillEnd: 310, height: 4 },
  buttonSlots: [
    { id: 'left', strokePath: 'M5 787.902L8.5 782.328H132V792.919M132 811.736V822H5.5V811.736' },
    { id: 'center', strokePath: 'M145 787.902L148.5 782.328H272V792.919M272 811.736V822H145.5V811.736' },
    { id: 'right', strokePath: 'M285 787.902L288.5 782.328H412V792.919M412 811.736V822H285.5V811.736' },
  ],
} as const

const lsInner = LEFT_SIDEBAR_SHELL.innerScreen

/**
 * 内容画布在裁剪区内的位置：
 * 横向按扩展后的内腔居中，纵向抵消原稿顶部留白，使首个标题贴近上边框。
 */
export const LEFT_SIDEBAR_CONTENT_OFFSET = {
  x:
    (lsInner.x + lsInner.width / 2) * LEFT_SIDEBAR_SHELL_SCALE_X -
    (LEFT_SIDEBAR_CONTENT_WIDTH * LEFT_SIDEBAR_CONTENT_SCALE) / 2 -
    LEFT_SIDEBAR_CLIP_INSET_LEFT,
  y: -14,
} as const

/** 底部控制区（417×870 内容坐标，与按钮 HTML 对齐） */
export const LEFT_SIDEBAR_BOTTOM_CHROME = {
  progressRail: LEFT_SIDEBAR_SHELL.progressRail,
  buttonSlots: LEFT_SIDEBAR_SHELL.buttonSlots,
  controls: {
    left: 5,
    top: 782,
    width: 407,
    height: 40,
    slots: [
      { left: 5, width: 127 },
      { left: 145, width: 127 },
      { left: 285, width: 127 },
    ],
  },
} as const
