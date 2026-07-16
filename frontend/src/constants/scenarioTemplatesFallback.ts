import type { ScenarioTemplate } from '../types/scenario'

/** 后端未就绪时的本地场景模板（与 Mock 规格 / citypulse 原型一致） */
export const FALLBACK_SCENARIO_TEMPLATES: ScenarioTemplate[] = [
  {
    template_id: 'xiongan20',
    name: '雄安窄路密网20路口',
    intersection_count: 20,
    description: '启动区窄路密网典型通勤场景，覆盖主干路与支路交织区域。',
    map_center: [115.9348, 39.0631],
    map_bounds: [115.928, 39.059, 115.941, 39.067],
    default_zoom: 15,
  },
  {
    template_id: 'corridor4',
    name: '4路口走廊控制',
    intersection_count: 4,
    description: '南北向走廊协调控制实验场景，适合多路口 IPPO 对比。',
    map_center: [115.9312, 39.0645],
    map_bounds: [115.929, 39.0625, 115.934, 39.066],
    default_zoom: 16,
  },
  {
    template_id: 'school',
    name: '学校周边人车混行',
    intersection_count: 8,
    description: '学校片区上下学高峰，行人混行与接送车辆交织。',
    map_center: [115.9385, 39.0602],
    map_bounds: [115.936, 39.058, 115.941, 39.062],
    default_zoom: 16,
  },
  {
    template_id: 'event',
    name: '大型活动散场疏散',
    intersection_count: 12,
    description: '活动场馆散场流量突增，重点观察排队溢出与走廊优先策略。',
    map_center: [115.9325, 39.0662],
    map_bounds: [115.929, 39.064, 115.936, 39.068],
    default_zoom: 15,
  },
]
