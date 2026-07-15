import { computed, ref, watch, type Ref } from 'vue'
import { createScenario } from '../api/scenario'
import { MOCK_RUN_ID } from '../constants/dashboardMockData'
import { useOptionalAppMapView } from './useAppMapView'
import {
  DISTURBANCE_CHOICE_OPTIONS,
  OD_PRESET_OPTIONS,
  TRAFFIC_FLOW_MODE_OPTIONS,
  type OdPresetId,
} from '../constants/scenarioOptions'
import type {
  CreateScenarioRequest,
  DisturbanceEvent,
  DisturbanceType,
  ScenarioTemplate,
  TrafficFlowMode,
} from '../types/scenario'
import {
  buildSubmitPayload,
  createDefaultDisturbance,
  createDefaultForm,
} from '../utils/scenarioPayload'

export interface CompactScenarioConfig {
  template_id: string
  flow_mode: TrafficFlowMode
  od_preset_id: OdPresetId
  disturbance: DisturbanceType | 'none'
  flow_scale: number
  duration: number
}

function defaultCompactConfig(): CompactScenarioConfig {
  return {
    template_id: '',
    flow_mode: 'morning_peak',
    od_preset_id: 'main_school',
    disturbance: 'lane_closure',
    flow_scale: 1.2,
    duration: 600,
  }
}

function resolveDisturbance(
  choice: DisturbanceType | 'none',
  duration: number,
): DisturbanceEvent[] {
  if (choice === 'none') {
    return []
  }

  const disturbance = createDefaultDisturbance(choice)
  if ('duration' in disturbance && disturbance.duration > duration) {
    return [{ ...disturbance, duration: Math.min(disturbance.duration, duration) }]
  }
  return [disturbance]
}

export function buildCompactScenarioPayload(
  config: CompactScenarioConfig,
  templates: ScenarioTemplate[],
): CreateScenarioRequest {
  const template = templates.find((item) => item.template_id === config.template_id)
  const odPreset =
    OD_PRESET_OPTIONS.find((item) => item.id === config.od_preset_id) ?? OD_PRESET_OPTIONS[0]

  const form = createDefaultForm()
  form.name = template?.name ?? `scenario_${Date.now()}`
  form.template_id = config.template_id
  form.traffic_flow = {
    ...form.traffic_flow,
    mode: config.flow_mode,
    flow_scale: config.flow_scale,
    duration: config.duration,
  }
  form.od_groups = [
    {
      od_id: 'od_001',
      origin: odPreset.origin,
      destination: odPreset.destination,
      vehicles_per_hour: 800,
      start_time: 0,
      end_time: config.duration,
    },
  ]
  form.disturbances = resolveDisturbance(config.disturbance, config.duration)

  return buildSubmitPayload(form)
}

export function useCompactScenarioConfig(
  templates: Ref<ScenarioTemplate[]>,
  initialScenarioId?: Ref<string>,
) {
  const config = ref<CompactScenarioConfig>(defaultCompactConfig())
  const scenarioId = ref(initialScenarioId?.value ?? '')
  const generating = ref(false)
  const generateError = ref<string | null>(null)
  const generateSuccessNote = ref<string | null>(null)
  const mapView = useOptionalAppMapView()

  watch(
    () => config.value.template_id,
    (templateId) => {
      if (!mapView) {
        return
      }
      if (templateId) {
        mapView.flyToTemplate(templateId)
        return
      }
      mapView.resetToDefault()
    },
  )

  watch(
    templates,
    (items) => {
      if (!config.value.template_id && items.length > 0) {
        config.value.template_id = items[0].template_id
      }
    },
    { immediate: true },
  )

  if (initialScenarioId) {
    watch(initialScenarioId, (value) => {
      if (value) {
        scenarioId.value = value
      }
    })
  }

  const selectedTemplate = computed(
    () => templates.value.find((item) => item.template_id === config.value.template_id) ?? null,
  )

  const configNote = computed(() => {
    const templateName = selectedTemplate.value?.name ?? '未选择场景模板'
    const flowLabel =
      TRAFFIC_FLOW_MODE_OPTIONS.find((item) => item.value === config.value.flow_mode)?.label ??
      config.value.flow_mode
    const odLabel =
      OD_PRESET_OPTIONS.find((item) => item.id === config.value.od_preset_id)?.label ??
      config.value.od_preset_id
    const disturbanceLabel =
      DISTURBANCE_CHOICE_OPTIONS.find((item) => item.value === config.value.disturbance)?.label ??
      config.value.disturbance

    return `当前配置：${templateName}，${flowLabel}，OD为${odLabel}，流量倍率${config.value.flow_scale}x，注入${disturbanceLabel}扰动。`
  })

  const footerNote = computed(() => generateSuccessNote.value ?? configNote.value)

  async function generateScenario() {
    if (!config.value.template_id) {
      generateError.value = '请选择场景模板'
      return null
    }

    generating.value = true
    generateError.value = null

    try {
      const payload = buildCompactScenarioPayload(config.value, templates.value)
      const result = await createScenario(payload)
      scenarioId.value = result.scenario_id
      mapView?.flyToScenario(result.scenario_id, config.value.template_id)
      generateSuccessNote.value =
        '已生成 SUMO 配置文件：net.xml、rou.xml、add.xml、sumocfg，并完成场景参数记录。'
      window.setTimeout(() => {
        generateSuccessNote.value = null
      }, 2400)
      return result
    } catch {
      const mockScenarioId = `scenario_${MOCK_RUN_ID.replace('run_', '')}`
      scenarioId.value = mockScenarioId
      mapView?.flyToTemplate(config.value.template_id)
      generateError.value = null
      generateSuccessNote.value = '已使用本地 Mock 场景配置（后端未连接）。'
      window.setTimeout(() => {
        generateSuccessNote.value = null
      }, 2400)
      return { scenario_id: mockScenarioId }
    } finally {
      generating.value = false
    }
  }

  return {
    config,
    scenarioId,
    generating,
    generateError,
    footerNote,
    configNote,
    selectedTemplate,
    generateScenario,
  }
}
