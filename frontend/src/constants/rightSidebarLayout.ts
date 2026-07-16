/** 右侧数据面板布局常量 */

import { CHROME_SIDEBAR_CENTER_EXPAND } from './dashboardChromeLayout'

/** 原稿外壳宽度（扩展前，用于固定右侧与边框的锚点） */
export const RIGHT_SIDEBAR_DESIGN_WIDTH_BASE = 560
/** 外壳画布（略宽于左侧，向左延伸） */
export const RIGHT_SIDEBAR_DESIGN_WIDTH = RIGHT_SIDEBAR_DESIGN_WIDTH_BASE + CHROME_SIDEBAR_CENTER_EXPAND
export const RIGHT_SIDEBAR_DESIGN_HEIGHT = 990

/** 内容逻辑画布（原 右侧数据.svg 坐标系） */
export const RIGHT_SIDEBAR_CONTENT_WIDTH = 465
export const RIGHT_SIDEBAR_CONTENT_HEIGHT = 870

/** 裁剪安全边距（外壳 px，仅避开内腔描边） */
export const RIGHT_SIDEBAR_CONTENT_SAFE_INSET_LEFT = 3
export const RIGHT_SIDEBAR_CONTENT_SAFE_INSET_TOP = 3
export const RIGHT_SIDEBAR_CONTENT_SAFE_INSET_RIGHT = 3
export const RIGHT_SIDEBAR_CONTENT_SAFE_INSET_BOTTOM = 8

/** 底部斜角区域额外裁剪（外壳 px） */
export const RIGHT_SIDEBAR_CLIP_BOTTOM_CHAMFER_EXTRA = 14

/** 内屏内容区不对称留白：向左上扩展，右侧/底部保持安全距离 */
export const RIGHT_SIDEBAR_INNER_MARGIN_LEFT = 4
export const RIGHT_SIDEBAR_INNER_MARGIN_RIGHT = 10

/** 外壳 SVG 相对内容画布的等比缩放 */
export const RIGHT_SIDEBAR_SHELL_SCALE_X =
  RIGHT_SIDEBAR_DESIGN_WIDTH / RIGHT_SIDEBAR_CONTENT_WIDTH
export const RIGHT_SIDEBAR_SHELL_SCALE_Y =
  RIGHT_SIDEBAR_DESIGN_HEIGHT / RIGHT_SIDEBAR_CONTENT_HEIGHT

/** 外壳装饰（465×870 原稿坐标） */
export const RIGHT_SIDEBAR_SHELL = {
  /** 主面板内腔底边（斜角前水平线） */
  frameInnerBottom: 829,
  innerScreen: { x: 10.9548, y: 4, width: 411.363, height: 825, rx: 12 },
} as const

const rsInner = RIGHT_SIDEBAR_SHELL.innerScreen

/** 内容区（相对 465×870 内容画布，在扩展内腔中水平居中） */
export const RIGHT_SIDEBAR_CONTENT_BLOCK = {
  left: 34,
  width: 425,
} as const

const rsBlock = RIGHT_SIDEBAR_CONTENT_BLOCK
const rsBlockRight = rsBlock.left + rsBlock.width

/** 裁剪区：贴合内屏区域，避免内容与外侧机械边框重叠 */
export const RIGHT_SIDEBAR_CLIP_INSET_LEFT = Math.round(
  rsInner.x * RIGHT_SIDEBAR_SHELL_SCALE_X + RIGHT_SIDEBAR_CONTENT_SAFE_INSET_LEFT,
)
export const RIGHT_SIDEBAR_CLIP_INSET_TOP = Math.round(
  rsInner.y * RIGHT_SIDEBAR_SHELL_SCALE_Y + RIGHT_SIDEBAR_CONTENT_SAFE_INSET_TOP,
)
export const RIGHT_SIDEBAR_CLIP_INSET_RIGHT = Math.round(
  RIGHT_SIDEBAR_DESIGN_WIDTH -
    (rsInner.x + rsInner.width) * RIGHT_SIDEBAR_SHELL_SCALE_X +
    RIGHT_SIDEBAR_CONTENT_SAFE_INSET_RIGHT,
)
export const RIGHT_SIDEBAR_CLIP_INSET_BOTTOM = Math.round(
  RIGHT_SIDEBAR_DESIGN_HEIGHT -
    RIGHT_SIDEBAR_SHELL.frameInnerBottom * RIGHT_SIDEBAR_SHELL_SCALE_Y +
    RIGHT_SIDEBAR_CONTENT_SAFE_INSET_BOTTOM +
    RIGHT_SIDEBAR_CLIP_BOTTOM_CHAMFER_EXTRA,
)

const RS_CLIP_WIDTH =
  RIGHT_SIDEBAR_DESIGN_WIDTH -
  RIGHT_SIDEBAR_CLIP_INSET_LEFT -
  RIGHT_SIDEBAR_CLIP_INSET_RIGHT
const RS_CLIP_HEIGHT =
  RIGHT_SIDEBAR_DESIGN_HEIGHT -
  RIGHT_SIDEBAR_CLIP_INSET_TOP -
  RIGHT_SIDEBAR_CLIP_INSET_BOTTOM

/** 在裁剪区内尽量放大 */
export const RIGHT_SIDEBAR_CONTENT_SCALE = Math.min(
  1.08,
  Math.floor((RS_CLIP_WIDTH / RIGHT_SIDEBAR_CONTENT_WIDTH) * 1000) / 1000,
  Math.floor(((RS_CLIP_HEIGHT + 28) / RIGHT_SIDEBAR_CONTENT_HEIGHT) * 1000) / 1000,
)

/**
 * 内容画布在裁剪区内的位置：
 * 横向按扩展后的内腔居中，纵向抵消原稿顶部留白。
 */
export const RIGHT_SIDEBAR_CONTENT_OFFSET = {
  x:
    (rsInner.x + rsInner.width / 2) * RIGHT_SIDEBAR_SHELL_SCALE_X -
    (RIGHT_SIDEBAR_CONTENT_WIDTH * RIGHT_SIDEBAR_CONTENT_SCALE) / 2 -
    RIGHT_SIDEBAR_CLIP_INSET_LEFT,
  y: 14,
} as const

/** 区块标题栏 */
export const RIGHT_SIDEBAR_SECTION_HEADERS = {
  communication: {
    left: rsBlock.left,
    top: 32,
    width: rsBlock.width,
    height: 40,
    viewBox: '0 0 368 40',
  },
  metrics: {
    left: rsBlock.left,
    top: 378,
    width: rsBlock.width,
    height: 40,
    viewBox: '0 0 368 40',
  },
} as const

/** 通信日志表 */
export const RIGHT_SIDEBAR_COMMUNICATION_TABLE = {
  head: { left: rsBlock.left, top: 86, width: rsBlock.width, height: 40 },
  body: { left: rsBlock.left, top: 126, width: rsBlock.width, height: 224 },
  columns: { time: 108, flow: 112 },
  rowMinHeight: 32,
} as const

/** 导出按钮（右对齐，距内容区右缘 10px） */
export const RIGHT_SIDEBAR_EXPORT_BUTTON = {
  left: rsBlockRight - 10 - 103,
  top: 422,
  width: 103,
  height: 32.18,
  rx: 16.09,
} as const

const chartPlotLeft = rsBlock.left + 4
const chartPlotWidth = rsBlock.width - 8

/** 指标图表区 */
export const RIGHT_SIDEBAR_CHARTS = {
  queue: {
    titleTop: 428,
    titleLeft: rsBlock.left,
    plot: { left: chartPlotLeft, top: 468, width: chartPlotWidth, height: 147 },
    gridLines: [484, 516, 548, 580],
    separatorY: 613,
  },
  waiting: {
    titleTop: 646,
    titleLeft: rsBlock.left,
    plot: { left: chartPlotLeft, top: 686, width: chartPlotWidth, height: 141 },
    gridLines: [702, 732, 762, 792],
    separatorY: 825,
  },
  separatorBand: {
    left: rsBlock.left + rsBlock.width * 0.5,
    top: 450,
    width: rsBlock.width * 0.65,
    height: 14.22,
  },
} as const
