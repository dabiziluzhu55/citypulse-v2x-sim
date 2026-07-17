import type { NetworkSource, TrafficFlowMode, TrafficLightPlan, DisturbanceType } from '../types/scenario'

export interface SelectOption<T extends string = string> {
  label: string
  value: T
}

export const NETWORK_SOURCE_OPTIONS: SelectOption<NetworkSource>[] = [
  { label: 'OSM 导入', value: 'osm_import' },
  { label: '已处理 SUMO 路网', value: 'prebuilt_sumo' },
  { label: '手动 netedit 路网', value: 'manual_netedit' },
]

export const TRAFFIC_FLOW_MODE_OPTIONS: SelectOption<TrafficFlowMode>[] = [
  { label: '平峰', value: 'flat' },
  { label: '早高峰', value: 'morning_peak' },
  { label: '晚高峰', value: 'evening_peak' },
]

export const FLOW_SCALE_OPTIONS = [0.8, 1.0, 1.2, 1.5] as const

export const DURATION_OPTIONS = [
  { label: '10min', value: 600 },
  { label: '30 分钟', value: 1800 },
  { label: '60 分钟', value: 3600 },
  { label: '120 分钟', value: 7200 },
] as const

export const OD_PRESET_OPTIONS = [
  {
    id: 'res_office',
    label: '居住区 → 办公区',
    origin: 'residential_area_A',
    destination: 'office_area_B',
  },
  {
    id: 'main_school',
    label: '主干路 → 学校周边',
    origin: 'main_road_entrance',
    destination: 'school',
  },
  {
    id: 'event_parking',
    label: '活动场馆 → 停车场',
    origin: 'school_zone_entrance',
    destination: 'parking_lot',
  },
  {
    id: 'detour',
    label: '施工绕行 OD',
    origin: 'main_road_entrance',
    destination: 'main_road_exit',
  },
] as const

export type OdPresetId = (typeof OD_PRESET_OPTIONS)[number]['id']

export const ORIGIN_OPTIONS: SelectOption[] = [
  { label: '居住区出入口', value: 'residential_area_A' },
  { label: '主干道入口', value: 'main_road_entrance' },
  { label: '学校周边入口', value: 'school_zone_entrance' },
]

export const DESTINATION_OPTIONS: SelectOption[] = [
  { label: '办公区', value: 'office_area_B' },
  { label: '学校', value: 'school' },
  { label: '停车场', value: 'parking_lot' },
  { label: '主干道出口', value: 'main_road_exit' },
]

export const TRAFFIC_LIGHT_PLAN_OPTIONS: SelectOption<TrafficLightPlan>[] = [
  { label: '固定配时', value: 'fixed_time' },
  { label: '默认 SUMO 方案', value: 'default_sumo' },
  { label: '自定义方案', value: 'custom' },
]

export const DISTURBANCE_TYPE_OPTIONS: SelectOption<DisturbanceType>[] = [
  { label: '施工占道', value: 'lane_closure' },
  { label: '道路限速', value: 'speed_limit' },
  { label: '交通事故', value: 'accident' },
]

export const DISTURBANCE_CHOICE_OPTIONS: SelectOption<DisturbanceType | 'none'>[] = [
  ...DISTURBANCE_TYPE_OPTIONS,
  { label: '无扰动', value: 'none' },
]

export const FLOW_SCALE_SELECT_OPTIONS = FLOW_SCALE_OPTIONS.map((value) => ({
  label: `${value}x`,
  value,
}))

export const OD_TIME_PRESETS = [
  { label: '全程', start: 0, endKey: 'duration' as const },
  { label: '前 30 分钟', start: 0, end: 1800 },
  { label: '30–60 分钟', start: 1800, end: 3600 },
] as const
