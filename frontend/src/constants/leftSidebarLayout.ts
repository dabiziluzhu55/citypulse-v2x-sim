/** 左侧数据面板统一布局常量（参考稿 439×870） */
export const LEFT_SIDEBAR_DESIGN_WIDTH = 439
export const LEFT_SIDEBAR_DESIGN_HEIGHT = 870
export const LEFT_SIDEBAR_CONTENT_WIDTH = LEFT_SIDEBAR_DESIGN_WIDTH
export const LEFT_SIDEBAR_CONTENT_HEIGHT = LEFT_SIDEBAR_DESIGN_HEIGHT

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
    top: 405,
    width: 354.151,
    height: 40,
    viewBox: '0 0 354.151 40',
  },
} as const

export const LEFT_SIDEBAR_SHELL_SCALE_X = 1
export const LEFT_SIDEBAR_SHELL_SCALE_Y = 1

export const LEFT_SIDEBAR_REFERENCE_LAYOUT = {
  fields: [
    { key: 'scenario', left: 28, top: 91, width: 155, height: 66 },
    { key: 'disturbance', left: 202, top: 91, width: 155, height: 66 },
    { key: 'flow', left: 28, top: 172, width: 155, height: 66 },
    { key: 'time', left: 202, top: 172, width: 155, height: 66 },
  ],
  summary: { left: 32, top: 252, width: 330, height: 51 },
  fileActions: { left: 28, top: 312, width: 334, height: 42, gap: 12 },
  algorithmItems: { left: 35, top: 463, width: 328, height: 34, gap: 11 },
  speedBadge: { left: 315, top: 663, width: 68, height: 34 },
  speedMenu: { left: 315, bottom: 212, width: 68, optionHeight: 30 },
} as const

export const LEFT_SIDEBAR_SHELL = {
  innerScreen: { x: 10.5818, y: 51, width: 381.934, height: 426.754, rx: 26 },
  progressRail: { y: 680, x1: 25, x2: 288, fillEnd: 288, height: 3 },
  buttonSlots: [
    { id: 'left', strokePath: 'M21 708.574L24.459 703H136.539V713.591M136.539 732.408V742.672H21.461V732.408' },
    { id: 'center', strokePath: 'M153 708.574L156.459 703H268.539V713.591M268.539 732.408V742.672H153.461V732.408' },
    { id: 'right', strokePath: 'M287 708.574L290.459 703H402.539V713.591M402.539 732.408V742.672H287.461V732.408' },
  ],
} as const

export const LEFT_SIDEBAR_BOTTOM_CHROME = {
  progressRail: LEFT_SIDEBAR_SHELL.progressRail,
  buttonSlots: LEFT_SIDEBAR_SHELL.buttonSlots,
  controls: {
    left: 21,
    top: 703,
    width: 382,
    height: 40,
    slots: [
      { left: 21, width: 116 },
      { left: 153, width: 116 },
      { left: 287, width: 116 },
    ],
  },
} as const
