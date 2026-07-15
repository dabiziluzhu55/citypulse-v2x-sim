/** 场景模板展示用元数据（地点标签、accent 色） */
export interface ScenarioLocationMeta {
  template_id: string
  areaTag: string
  highlight: string
  accent: 'cyan' | 'blue' | 'yellow' | 'green'
}

export const SCENARIO_LOCATION_META: Record<string, ScenarioLocationMeta> = {
  xiongan20: {
    template_id: 'xiongan20',
    areaTag: '启动区',
    highlight: '20 路口密网',
    accent: 'cyan',
  },
  corridor4: {
    template_id: 'corridor4',
    areaTag: '走廊轴',
    highlight: '4 路口协调',
    accent: 'blue',
  },
  school: {
    template_id: 'school',
    areaTag: '学校片区',
    highlight: '人车混行',
    accent: 'yellow',
  },
  event: {
    template_id: 'event',
    areaTag: '活动场馆',
    highlight: '散场疏散',
    accent: 'green',
  },
}

export function resolveLocationMeta(templateId: string): ScenarioLocationMeta {
  return (
    SCENARIO_LOCATION_META[templateId] ?? {
      template_id: templateId,
      areaTag: '仿真区域',
      highlight: templateId,
      accent: 'cyan',
    }
  )
}
