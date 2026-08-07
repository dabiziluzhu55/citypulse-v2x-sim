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
    intersectionIds: Array.from({ length: 20 }, (_, index) => `demo_${index + 1}`),
  },
  {
    label: '东部密集路口场景',
    value: 'east_dense',
    intersectionIds: ['demo_3', 'demo_5', 'demo_6', 'demo_9'],
  },
  {
    label: '西部密集路口场景',
    value: 'west_dense',
    intersectionIds: ['demo_14', 'demo_15', 'demo_19'],
  },
] as const

export type ScenarioModeId = string

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

export interface SimulationPeriodRange {
  start: string
  end: string
}

export const SIMULATION_PERIOD_RANGES: Record<TrafficFlowMode, SimulationPeriodRange> = {
  morning_peak: { start: '07:00', end: '09:00' },
  flat: { start: '14:30', end: '16:30' },
  evening_peak: { start: '17:30', end: '19:30' },
}

export const MAX_SIMULATION_DURATION_MINUTES = 15
export const MAX_SIMULATION_DURATION_SECONDS = MAX_SIMULATION_DURATION_MINUTES * 60

export interface SimulationClockPartOptions {
  hours: string[]
  minutesByHour: Record<string, string[]>
}

export function clockTimeToMinutes(value: string): number {
  const match = /^(\d{2}):(\d{2})$/.exec(value)
  if (!match) return Number.NaN
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (hours > 23 || minutes > 59) return Number.NaN
  return hours * 60 + minutes
}

export function minutesToClockTime(value: number): string {
  const minutes = Math.max(0, Math.min(24 * 60 - 1, Math.round(value)))
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

function clockValuesBetween(startMinutes: number, endMinutes: number): string[] {
  if (!Number.isFinite(startMinutes) || !Number.isFinite(endMinutes) || endMinutes < startMinutes) return []
  return Array.from(
    { length: endMinutes - startMinutes + 1 },
    (_, index) => minutesToClockTime(startMinutes + index),
  )
}

export function simulationStartClockValues(flowMode: TrafficFlowMode): string[] {
  const range = SIMULATION_PERIOD_RANGES[flowMode]
  return clockValuesBetween(
    clockTimeToMinutes(range.start),
    clockTimeToMinutes(range.end) - 1,
  )
}

export function simulationEndClockValues(flowMode: TrafficFlowMode, start: string): string[] {
  const rangeEnd = clockTimeToMinutes(SIMULATION_PERIOD_RANGES[flowMode].end)
  const startMinutes = clockTimeToMinutes(start)
  if (!Number.isFinite(startMinutes)) return []
  return clockValuesBetween(
    startMinutes + 1,
    Math.min(rangeEnd, startMinutes + MAX_SIMULATION_DURATION_MINUTES),
  )
}

export function disabledClockHours(allowedValues: string[]): number[] {
  const allowedHours = new Set(
    allowedValues
      .map((value) => clockTimeToMinutes(value))
      .filter(Number.isFinite)
      .map((value) => Math.floor(value / 60)),
  )
  return Array.from({ length: 24 }, (_, hour) => hour)
    .filter((hour) => !allowedHours.has(hour))
}

export function disabledClockMinutes(allowedValues: string[], hour: number): number[] {
  const allowedMinutes = new Set(
    allowedValues
      .map((value) => clockTimeToMinutes(value))
      .filter((value) => Number.isFinite(value) && Math.floor(value / 60) === hour)
      .map((value) => value % 60),
  )
  return Array.from({ length: 60 }, (_, minute) => minute)
    .filter((minute) => !allowedMinutes.has(minute))
}

export function clockPartOptions(values: string[]): SimulationClockPartOptions {
  const minutesByHour: Record<string, string[]> = {}
  values.forEach((value) => {
    const [hour, minute] = value.split(':')
    if (!hour || !minute) return
    const minutes = minutesByHour[hour] ?? []
    if (!minutes.includes(minute)) minutes.push(minute)
    minutesByHour[hour] = minutes
  })
  return { hours: Object.keys(minutesByHour), minutesByHour }
}

export function clockTimeParts(value: string): { hour: string; minute: string } {
  const [hour = '', minute = ''] = value.split(':')
  return { hour, minute }
}

export function maximumSimulationEndTime(flowMode: TrafficFlowMode, start: string): string {
  const rangeEnd = clockTimeToMinutes(SIMULATION_PERIOD_RANGES[flowMode].end)
  const startMinutes = clockTimeToMinutes(start)
  if (!Number.isFinite(startMinutes)) return SIMULATION_PERIOD_RANGES[flowMode].end
  return minutesToClockTime(Math.min(rangeEnd, startMinutes + MAX_SIMULATION_DURATION_MINUTES))
}

export function defaultSimulationTimeWindow(flowMode: TrafficFlowMode): { start: string; end: string } {
  const start = SIMULATION_PERIOD_RANGES[flowMode].start
  return { start, end: maximumSimulationEndTime(flowMode, start) }
}

export function simulationTimeWindow(
  flowMode: TrafficFlowMode,
  start: string,
  end: string,
): { windowStartSeconds: number; durationSeconds: number } {
  const range = SIMULATION_PERIOD_RANGES[flowMode]
  const rangeStart = clockTimeToMinutes(range.start)
  const rangeEnd = clockTimeToMinutes(range.end)
  const startMinutes = clockTimeToMinutes(start)
  const endMinutes = clockTimeToMinutes(end)
  if (
    !Number.isFinite(startMinutes)
    || !Number.isFinite(endMinutes)
    || startMinutes < rangeStart
    || endMinutes > rangeEnd
    || endMinutes <= startMinutes
    || endMinutes - startMinutes > MAX_SIMULATION_DURATION_MINUTES
  ) {
    throw new Error(`Simulation time must stay within ${range.start}-${range.end} and not exceed 15 minutes`)
  }
  return {
    windowStartSeconds: (startMinutes - rangeStart) * 60,
    durationSeconds: (endMinutes - startMinutes) * 60,
  }
}

export const DEFAULT_PLAYBACK_SPEED_OPTIONS = [1, 1.25, 1.5, 2, 3, 5] as const

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

export type DisturbancePresetId =
  | 'construction'
  | 'event_departure'
  | 'event_arrival'
  | 'accident'
  | 'speed_limit'

export interface DisturbanceEventOption extends SelectOption<DisturbancePresetId> {
  eventType: DisturbanceType
}

export const DISTURBANCE_EVENT_OPTIONS: DisturbanceEventOption[] = [
  { label: '施工占道', value: 'construction', eventType: 'lane_closure' },
  { label: '道路限速', value: 'speed_limit', eventType: 'speed_limit' },
  { label: '大型活动散场', value: 'event_departure', eventType: 'major_event_closing' },
  { label: '大型活动开场', value: 'event_arrival', eventType: 'major_event_opening' },
  { label: '交通事故', value: 'accident', eventType: 'accident' },
]

export const DEFAULT_BACKEND_EVENT_TYPES: DisturbanceType[] = [
  'lane_closure',
  'speed_limit',
  'accident',
  'major_event_opening',
  'major_event_closing',
]

export function resolveCatalogEventTypes(eventTypes: string[] | null | undefined): string[] {
  return eventTypes == null ? [...DEFAULT_BACKEND_EVENT_TYPES] : [...eventTypes]
}

export function resolveCatalogPlaybackSpeeds(playbackSpeeds: number[] | null | undefined): number[] {
  return playbackSpeeds == null ? [...DEFAULT_PLAYBACK_SPEED_OPTIONS] : [...playbackSpeeds]
}

export const DISTURBANCE_CHOICE_OPTIONS: SelectOption<DisturbanceType | 'none'>[] = [
  ...DISTURBANCE_TYPE_OPTIONS,
  { label: '无扰动', value: 'none' },
]

export const PLAYBACK_SPEED_SELECT_OPTIONS = DEFAULT_PLAYBACK_SPEED_OPTIONS.map((value) => ({
  label: `${value}x`,
  value,
}))

export const OD_TIME_PRESETS = [
  { label: '全程', start: 0, endKey: 'duration' as const },
  { label: '前 30 分钟', start: 0, end: 1800 },
  { label: '30–60 分钟', start: 1800, end: 3600 },
] as const
