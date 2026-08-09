/** 左侧数据面板统一布局常量 */

import { CHROME_SIDEBAR_CENTER_EXPAND } from './dashboardChromeLayout'

/** 内容逻辑画布 */
export const LEFT_SIDEBAR_CONTENT_WIDTH = 439
export const LEFT_SIDEBAR_CONTENT_HEIGHT = 870

/** 原稿外壳宽度 */
export const LEFT_SIDEBAR_DESIGN_WIDTH_BASE = 560
/** 外壳画布 */
export const LEFT_SIDEBAR_DESIGN_WIDTH = LEFT_SIDEBAR_DESIGN_WIDTH_BASE + CHROME_SIDEBAR_CENTER_EXPAND
export const LEFT_SIDEBAR_DESIGN_HEIGHT = 990

/**
 * 内容等比缩放
 * 高度铺满外壳，宽度随比例自然变化
 */
export const LEFT_SIDEBAR_CONTENT_SCALE =
  LEFT_SIDEBAR_DESIGN_HEIGHT / LEFT_SIDEBAR_CONTENT_HEIGHT

/** 内容水平居中 */
export const LEFT_SIDEBAR_CONTENT_OFFSET_X =
  (LEFT_SIDEBAR_DESIGN_WIDTH_BASE -
    LEFT_SIDEBAR_CONTENT_WIDTH * LEFT_SIDEBAR_CONTENT_SCALE) /
  2
export const LEFT_SIDEBAR_CONTENT_OFFSET_Y = 0

/**
 * @deprecated 请优先使用 LEFT_SIDEBAR_CONTENT_SCALE
 */
export const LEFT_SIDEBAR_SHELL_SCALE_X =
  LEFT_SIDEBAR_DESIGN_WIDTH_BASE / LEFT_SIDEBAR_CONTENT_WIDTH
export const LEFT_SIDEBAR_SHELL_SCALE_Y =
  LEFT_SIDEBAR_DESIGN_HEIGHT / LEFT_SIDEBAR_CONTENT_HEIGHT

export const LEFT_SIDEBAR_SECTION_HEADERS = {
  scenario: {
    left: 28,
    top: 36,
    width: 368,
    height: 45,
    viewBox: '0 0 368 45',
  },
  algorithm: {
    left: 33,
    top: 490,
    width: 354.151,
    height: 40,
    viewBox: '0 0 354.151 40',
  },
} as const

/** 主内容列：与标题左右对齐 */
export const LEFT_SIDEBAR_CONTENT_COLUMN = {
  left: LEFT_SIDEBAR_SECTION_HEADERS.scenario.left,
  width: LEFT_SIDEBAR_SECTION_HEADERS.scenario.width,
  right:
    LEFT_SIDEBAR_SECTION_HEADERS.scenario.left +
    LEFT_SIDEBAR_SECTION_HEADERS.scenario.width,
} as const

const col = LEFT_SIDEBAR_CONTENT_COLUMN
/** 场景模式  */
const FIELD_COL_GAP = 19
const FIELD_COL_WIDTH = (col.width - FIELD_COL_GAP) / 2
const FIELD_COL_RIGHT_LEFT = col.left + FIELD_COL_WIDTH + FIELD_COL_GAP

export const LEFT_SIDEBAR_REFERENCE_LAYOUT = {
  fields: [
    { key: 'scenario', left: col.left, top: 91, width: FIELD_COL_WIDTH, height: 66 },
    { key: 'flow', left: FIELD_COL_RIGHT_LEFT, top: 91, width: FIELD_COL_WIDTH, height: 66 },
  ],
  disturbanceTargets: { left: col.left, top: 174, width: col.width, height: 66 },
  timeRange: { left: col.left, top: 251, width: col.width, height: 84 },
  summary: { left: col.left, top: 355, width: col.width, height: 50 },
  fileActions: {
    left: col.left,
    top: 420,
    width: col.width,
    height: 38,
    gap: 15,
    buttonWidth: (col.width - 12) / 2,
  },
  algorithmSelect: { left: col.left, top: 546, width: col.width, height: 40 },
  /** 相对原稿上移 15px，收紧「管控算法选择」下方空白 */
  speedBadge: {
    width: 90,
    height: 34,
    top: 645,
    left: col.right - 88,
  },
  speedMenu: { left: col.right - 88, bottom: 227, width: 90, optionHeight: 20 },
  runtime: {
    left: col.left,
    top: 758,
    width: col.width,
    height: 82,
    metricsGap: 12,
  },
} as const

export const LEFT_SIDEBAR_SHELL = {
  innerScreen: { x: 10.5818, y: 51, width: 381.934, height: 426.754, rx: 26 },
  progressRail: { y: 665, x1: 25, x2: 288, fillEnd: 288, height: 6 },
  buttonSlots: [
    { id: 'left', strokePath: 'M21 693.574L24.459 688H136.539V698.591M136.539 717.408V727.672H21.461V717.408' },
    { id: 'center', strokePath: 'M153 693.574L156.459 688H268.539V698.591M268.539 717.408V727.672H153.461V717.408' },
    { id: 'right', strokePath: 'M287 693.574L290.459 688H402.539V698.591M402.539 717.408V727.672H287.461V717.408' },
  ],
} as const

export const LEFT_SIDEBAR_BOTTOM_CHROME = {
  progressRail: LEFT_SIDEBAR_SHELL.progressRail,
  buttonSlots: LEFT_SIDEBAR_SHELL.buttonSlots,
  controls: {
    left: 21,
    top: 688,
    width: 382,
    height: 40,
    slots: [
      { left: 21, width: 116 },
      { left: 153, width: 116 },
      { left: 287, width: 116 },
    ],
  },
} as const
