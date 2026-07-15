/** 大屏外壳尺寸（来自 Figma / SVG 设计稿） */
export const CHROME_HEADER_HEIGHT = 129
export const CHROME_SIDE_FRAME_WIDTH = 179
/** 左右装饰边框相对视口边缘的外扩（向两侧偏移） */
export const CHROME_FRAME_EDGE_OUTSET = 14
/** 侧栏与视口边缘的安全间距（位于 frame SVG 内描边内侧） */
export const CHROME_PANEL_INSET = 30
/** 两侧数据栏向中心对称扩展量（外侧与左右边框距离保持不变） */
export const CHROME_SIDEBAR_CENTER_EXPAND = 40
/** 左上角品牌区位置（位于顶栏下方，避免被边框遮挡） */
export const CHROME_BRAND_INSET_LEFT = 82
export const CHROME_BRAND_INSET_TOP = 56
export const CHROME_CLOCK_INSET_TOP = 38
/** 右上角系统时间相对视口右边缘的内缩（越大越靠左） */
export const CHROME_CLOCK_INSET_RIGHT = 52
export const CHROME_SIDE_CONTENT_GAP = 12
export const CHROME_BOTTOM_CENTER_WIDTH = 629
export const CHROME_BOTTOM_CENTER_HEIGHT = 58
/** 中央台/图标台水平居中（bbox 半宽） */
export const CHROME_BOTTOM_CENTER_HALF = CHROME_BOTTOM_CENTER_WIDTH / 2
/** 横条与中央台斜切对接半宽（bottom-center.svg 底边 x≈44.91 / 584.09） */
export const CHROME_BOTTOM_CONNECT_HALF = 269.59
export const CHROME_BOTTOM_ICONS_WIDTH = 421
export const CHROME_BOTTOM_ICONS_HEIGHT = 82
export const CHROME_BOTTOM_RAIL_HEIGHT = 30
/** 横条/角装饰 SVG 原稿宽度（用于 background-size） */
export const CHROME_BOTTOM_RAIL_ART_WIDTH = 562
export const CHROME_CORNER_LEFT_BOTTOM_WIDTH = CHROME_BOTTOM_RAIL_ART_WIDTH
export const CHROME_CORNER_LEFT_BOTTOM_HEIGHT = 30
/** 角装饰仅显示 SVG 外侧帽宽度 */
export const CHROME_BOTTOM_CORNER_CAP_WIDTH = 100
/** 横条外侧帽宽度（接左边框/角装饰） */
export const CHROME_BOTTOM_RAIL_OUTER_CAP_WIDTH = CHROME_BOTTOM_CORNER_CAP_WIDTH
/** 横条内侧斜切帽宽度（对接中央台，固定不拉伸） */
export const CHROME_BOTTOM_RAIL_INNER_CAP_WIDTH = 90
/** 底部左右装饰角相对视口边缘的内缩（向中间靠拢，与顶部对称） */
export const CHROME_BOTTOM_CORNER_INSET = 16
/** 左边框内侧竖线与横条左缘对齐（frame-left.svg 内描边底角 x≈90 → 视口 76px） */
export const CHROME_BOTTOM_RAIL_FRAME_INNER_JOIN_SVG_X = 90
/** 左横条左缘：与左边框内侧底角对齐 */
export const CHROME_BOTTOM_RAIL_LEFT_EDGE_X =
  -CHROME_FRAME_EDGE_OUTSET + CHROME_BOTTOM_RAIL_FRAME_INNER_JOIN_SVG_X
/** 右横条右缘：与右边框内侧底角对齐（与左缘对称） */
export const CHROME_BOTTOM_RAIL_RIGHT_EDGE_X = CHROME_BOTTOM_RAIL_LEFT_EDGE_X
/** 角装饰与横条衔接间隙 */
export const CHROME_BOTTOM_RAIL_FRAME_GAP = 4
/** 左横条与中央台之间的空隙（px，参考设计稿斜切间留白） */
export const CHROME_BOTTOM_RAIL_CENTER_GAP = 500
/** 横条靠中央台一端额外内缩（px，左右对称） */
export const CHROME_BOTTOM_RAIL_CENTER_END_INSET = 100
/** @deprecated 使用 CHROME_BOTTOM_RAIL_CENTER_END_INSET */
export const CHROME_BOTTOM_RAIL_RIGHT_INSET = CHROME_BOTTOM_RAIL_CENTER_END_INSET
/** @deprecated 使用 CHROME_BOTTOM_RAIL_CENTER_END_INSET */
export const CHROME_BOTTOM_RAIL_LEFT_INSET = CHROME_BOTTOM_RAIL_CENTER_END_INSET
/** @deprecated 左横条改用 CHROME_BOTTOM_RAIL_CENTER_GAP */
export const CHROME_BOTTOM_RAIL_OVERLAP = 0
/** 左横条起点：角帽右缘 + 间隙（与侧栏宽度无关） */
export const CHROME_BOTTOM_RAIL_START_X =
  CHROME_BOTTOM_CORNER_INSET -
  CHROME_FRAME_EDGE_OUTSET +
  CHROME_BOTTOM_CORNER_CAP_WIDTH +
  CHROME_BOTTOM_RAIL_FRAME_GAP
/** 底层横条/角装饰贴底偏移 */
export const CHROME_BOTTOM_DOCK_OFFSET_Y = 12
/** 中央梯形台相对视口底边的抬高量（0 = 与角装饰同高贴底） */
export const CHROME_BOTTOM_CENTER_LIFT = 0
export const CHROME_BOTTOM_CENTER_OFFSET_Y =
  CHROME_BOTTOM_DOCK_OFFSET_Y + CHROME_BOTTOM_CENTER_LIFT
/** 图标台相对视口底边偏移（中央台之上） */
export const CHROME_BOTTOM_ICONS_OFFSET_Y = CHROME_BOTTOM_CENTER_OFFSET_Y + 8
/** @deprecated 使用 CHROME_BOTTOM_RAIL_START_X */
export const CHROME_BOTTOM_RAIL_LEFT = CHROME_BOTTOM_RAIL_START_X
export const CHROME_TOP_BORDER_HEIGHT = 79
export const CHROME_TOP_BORDER_MAX_WIDTH = 736
/** 顶部左右装饰角相对视口边缘的内缩（向中间靠拢） */
export const CHROME_TOP_CORNER_INSET = 16
/** 顶部左右装饰角向上偏移（负值向上） */
export const CHROME_TOP_CORNER_OFFSET_Y = -8
/** 顶部装饰角向中央标题延伸量（越大与标题重叠越多，越小越向外收缩） */
export const CHROME_TOP_CORNER_CENTER_BIAS = 40
export const CHROME_HEADER_TITLE_WIDTH = 775

export const DASHBOARD_TITLE = '城脉通途：车路云协同管控与仿真系统'
