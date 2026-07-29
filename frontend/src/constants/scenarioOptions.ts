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

export const SCENARIO_MODE_OPTIONS = [
  {
    label: '雄安20路口路网',
    value: 'xiongan_20',
    backendIntersectionId: 'demo_2',
    source: 'compatibility_preset',
  },
] as const

export type ScenarioModeId = (typeof SCENARIO_MODE_OPTIONS)[number]['value']

export const SIMULATION_TIME_OPTIONS = [
  { label: '7:00-7:15', value: 'morning_0700', flowMode: 'morning_peak', windowStartSeconds: 0, durationSeconds: 900 },
  { label: '7:15-7:30', value: 'morning_0715', flowMode: 'morning_peak', windowStartSeconds: 900, durationSeconds: 900 },
  { label: '7:30-7:45', value: 'morning_0730', flowMode: 'morning_peak', windowStartSeconds: 1800, durationSeconds: 900 },
  { label: '7:45-8:00', value: 'morning_0745', flowMode: 'morning_peak', windowStartSeconds: 2700, durationSeconds: 900 },
  { label: '8:00-8:15', value: 'morning_0800', flowMode: 'morning_peak', windowStartSeconds: 3600, durationSeconds: 900 },
  { label: '8:15-8:30', value: 'morning_0815', flowMode: 'morning_peak', windowStartSeconds: 4500, durationSeconds: 900 },
  { label: '8:30-8:45', value: 'morning_0830', flowMode: 'morning_peak', windowStartSeconds: 5400, durationSeconds: 900 },
  { label: '8:45-9:00', value: 'morning_0845', flowMode: 'morning_peak', windowStartSeconds: 6300, durationSeconds: 900 },
  { label: '14:30-14:45', value: 'off_peak_1430', flowMode: 'flat', windowStartSeconds: 0, durationSeconds: 900 },
  { label: '14:45-15:00', value: 'off_peak_1445', flowMode: 'flat', windowStartSeconds: 900, durationSeconds: 900 },
  { label: '15:00-15:15', value: 'off_peak_1500', flowMode: 'flat', windowStartSeconds: 1800, durationSeconds: 900 },
  { label: '15:15-15:30', value: 'off_peak_1515', flowMode: 'flat', windowStartSeconds: 2700, durationSeconds: 900 },
  { label: '15:30-15:45', value: 'off_peak_1530', flowMode: 'flat', windowStartSeconds: 3600, durationSeconds: 900 },
  { label: '15:45-16:00', value: 'off_peak_1545', flowMode: 'flat', windowStartSeconds: 4500, durationSeconds: 900 },
  { label: '16:00-16:15', value: 'off_peak_1600', flowMode: 'flat', windowStartSeconds: 5400, durationSeconds: 900 },
  { label: '16:15-16:30', value: 'off_peak_1615', flowMode: 'flat', windowStartSeconds: 6300, durationSeconds: 900 },
  { label: '17:30-17:45', value: 'evening_1730', flowMode: 'evening_peak', windowStartSeconds: 0, durationSeconds: 900 },
  { label: '17:45-18:00', value: 'evening_1745', flowMode: 'evening_peak', windowStartSeconds: 900, durationSeconds: 900 },
  { label: '18:00-18:15', value: 'evening_1800', flowMode: 'evening_peak', windowStartSeconds: 1800, durationSeconds: 900 },
  { label: '18:15-18:30', value: 'evening_1815', flowMode: 'evening_peak', windowStartSeconds: 2700, durationSeconds: 900 },
  { label: '18:30-18:45', value: 'evening_1830', flowMode: 'evening_peak', windowStartSeconds: 3600, durationSeconds: 900 },
  { label: '18:45-19:00', value: 'evening_1845', flowMode: 'evening_peak', windowStartSeconds: 4500, durationSeconds: 900 },
  { label: '19:00-19:15', value: 'evening_1900', flowMode: 'evening_peak', windowStartSeconds: 5400, durationSeconds: 900 },
  { label: '19:15-19:30', value: 'evening_1915', flowMode: 'evening_peak', windowStartSeconds: 6300, durationSeconds: 900 },
] as const

export type SimulationTimePresetId = (typeof SIMULATION_TIME_OPTIONS)[number]['value']

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
