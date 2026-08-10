import type { VehicleType } from '../cesium/traffic/TrafficModelRegistry'

/**
 * 三维车辆 GLB 模型配置。
 * 使用 Cesium 官方免费示例模型，放置于 frontend/public/models/ 下：
 *   - CesiumMilkTruck.glb（卡车/公交近似）
 *   - GroundVehicle.glb（轿车）
 * 可通过环境变量覆盖为自定义模型。
 */
const CAR_MODEL_URI =
  import.meta.env.VITE_CESIUM_CAR_MODEL_URI?.trim() || '/models/GroundVehicle.glb'
const TRUCK_MODEL_URI =
  import.meta.env.VITE_CESIUM_TRUCK_MODEL_URI?.trim() || '/models/CesiumMilkTruck.glb'

export const CESIUM_VEHICLE_MODEL_URIS: Record<VehicleType, string> = {
  passenger: CAR_MODEL_URI,
  bus: TRUCK_MODEL_URI,
  truck: TRUCK_MODEL_URI,
}

/**
 * 各官方模型的量纲缩放：GroundVehicle 约 3.9m 长，CesiumMilkTruck 约 6m 长，
 * 缩放到真实车长（轿车 4.6m / 公交 12m / 卡车 8.5m）。
 */
export const CESIUM_VEHICLE_MODEL_SCALES: Record<VehicleType, number> = {
  passenger: 1.15,
  bus: 2.0,
  truck: 1.4,
}

/** 车头朝向修正（度），叠加到 SUMO 航向 */
export const CESIUM_VEHICLE_HEADING_OFFSET_DEGREES = 0

/**
 * 实时插值延迟（秒）：时钟落后于最新采样点的时长，
 * 让 Cesium 在两次 snapshot 之间做插值而非外推，换取平滑。
 */
export const CESIUM_VEHICLE_INTERPOLATION_DELAY_SECONDS = 0.8

/** 车辆离场判定：超过该时长未更新则移除 entity（秒） */
export const CESIUM_VEHICLE_STALE_SECONDS = 3

/** 车辆贴地微抬升（米），避免陷入路面/3D Tiles */
export const CESIUM_VEHICLE_HEIGHT_OFFSET_METERS = 0.3

/** 远距离降级为点的相机距离阈值（米） */
export const CESIUM_VEHICLE_POINT_LOD_DISTANCE = 2600

/* ── 三维路网视觉参数 ── */

/** 单车道宽度（米），路面总宽 = lane_count × 该值 */
export const ROAD_LANE_WIDTH_METERS = 3.5

/** 无车道信息时的默认车道数 */
export const ROAD_DEFAULT_LANE_COUNT = 2

/** 路面最小/最大宽度约束（米），避免异常数据导致过窄或过宽 */
export const ROAD_MIN_WIDTH_METERS = 5
export const ROAD_MAX_WIDTH_METERS = 40

/** 路面沥青色（深蓝灰，半透明，贴合暗色大屏） */
export const ROAD_SURFACE_CSS = 'rgba(18, 32, 52, 0.82)'

/** 路缘发光描边色（亮青） */
export const ROAD_EDGE_GLOW_CSS = '#21e6ff'

/** 车道中心虚线色 */
export const ROAD_CENTERLINE_CSS = 'rgba(220, 240, 255, 0.75)'

/** 路缘描边线宽（像素） */
export const ROAD_EDGE_WIDTH_PX = 2.5

/** 中心虚线线宽（像素） */
export const ROAD_CENTERLINE_WIDTH_PX = 2

/** 路口发光圆环半径（米）与颜色 */
export const ROAD_JUNCTION_RADIUS_METERS = 9
export const ROAD_JUNCTION_GLOW_CSS = '#21e6ff'
export const ROAD_JUNCTION_CORE_CSS = '#bff6ff'
