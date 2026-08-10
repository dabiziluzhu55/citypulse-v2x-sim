import { scenarioPresetIntersectionIds } from './scenarioPresetRules.ts'

interface ScenarioArchiveManifest {
  schema_version?: number
  od_included?: boolean
  files?: Record<string, unknown>
  scenario_preset_id?: string
  controlled_intersection_ids?: unknown[]
  period?: string
  control_mode?: string
}

export interface ScenarioArchiveValidationInput {
  scenarioPresetId: string
  period: string
  controlMode: string
}

export interface ScenarioArchiveValidationResult {
  manifest: ScenarioArchiveManifest
  summary: string
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 格式无效`)
  }
  return value as Record<string, unknown>
}

function parseJson(text: string, label: string): Record<string, unknown> {
  try {
    return requireRecord(JSON.parse(text), label)
  } catch (cause) {
    if (cause instanceof SyntaxError) throw new Error(`${label} 不是有效 JSON`)
    throw cause
  }
}

function sortedIntersectionIds(values: string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(
    right,
    undefined,
    { numeric: true },
  ))
}

export async function validateScenarioArchive(
  blob: Blob,
  input: ScenarioArchiveValidationInput,
): Promise<ScenarioArchiveValidationResult> {
  const { default: JSZip } = await import('jszip')
  const zip = await JSZip.loadAsync(await blob.arrayBuffer())
  const manifestEntry = zip.file('export_manifest.json')
  if (!manifestEntry) throw new Error('场景 ZIP 缺少 export_manifest.json')

  const manifest = parseJson(
    await manifestEntry.async('string'),
    'export_manifest.json',
  ) as ScenarioArchiveManifest
  if (manifest.schema_version !== 1) {
    throw new Error(`场景 ZIP Schema 不受支持：${String(manifest.schema_version)}`)
  }
  if (manifest.scenario_preset_id !== input.scenarioPresetId) {
    throw new Error(`场景 ZIP 预设不匹配：${manifest.scenario_preset_id}`)
  }
  if (manifest.period !== input.period) throw new Error(`场景 ZIP 时段不匹配：${manifest.period}`)
  if (manifest.control_mode !== input.controlMode) {
    throw new Error(`场景 ZIP 管控算法不匹配：${manifest.control_mode}`)
  }

  const expectedIntersectionIds = sortedIntersectionIds(
    scenarioPresetIntersectionIds(input.scenarioPresetId),
  )
  const controlledIntersectionIds = Array.isArray(manifest.controlled_intersection_ids)
    ? manifest.controlled_intersection_ids.filter((value): value is string => typeof value === 'string')
    : []
  if (
    controlledIntersectionIds.length !== manifest.controlled_intersection_ids?.length
    || JSON.stringify(sortedIntersectionIds(controlledIntersectionIds)) !== JSON.stringify(expectedIntersectionIds)
  ) {
    throw new Error(`场景 ZIP 路口范围不匹配：应为 ${expectedIntersectionIds.length} 个路口`)
  }

  const files = requireRecord(manifest.files, 'export_manifest.files')
  for (const key of ['sumocfg', 'routes', 'additional', 'events', 'network']) {
    const path = files[key]
    if (typeof path !== 'string' || !path) throw new Error(`场景 manifest 缺少基础文件路径：${key}`)
    if (!zip.file(path)) throw new Error(`场景 ZIP 缺少 ${path}`)
  }

  const odEntries = Object.keys(zip.files).filter((name) => name.startsWith('od/'))
  if (input.scenarioPresetId !== 'xiongan_20') {
    if (manifest.od_included !== false) throw new Error('局部场景 manifest 必须标记 od_included=false')
    if (odEntries.some((name) => !zip.files[name].dir)) throw new Error('局部场景不应包含全域 OD 文件')
    return { manifest, summary: 'SUMO 场景 ZIP 已校验' }
  }

  if (manifest.od_included !== true) throw new Error('20 路口场景未包含 OD 数据')
  const expected = {
    od_matrix_csv: `od/od_matrix_${input.period}.csv`,
    od_taz_json: 'od/taz_9_zones.json',
    od_heatmap_png: `od/od_heatmap_${input.period}.png`,
  } as const
  for (const [key, path] of Object.entries(expected)) {
    if (files[key] !== path) throw new Error(`场景 manifest 路径不匹配：${key}`)
    if (!zip.file(path)) throw new Error(`场景 ZIP 缺少 ${path}`)
  }

  const csv = (await zip.file(expected.od_matrix_csv)!.async('string')).trim()
  const csvHeader = csv.split(/\r?\n/, 1)[0] ?? ''
  if (!csvHeader.includes('zone_1') || !csvHeader.includes('zone_9')) {
    throw new Error('OD 矩阵不是有效的九区域 CSV')
  }

  const taz = parseJson(
    await zip.file(expected.od_taz_json)!.async('string'),
    'TAZ JSON',
  )
  if (taz.zone_count !== 9) throw new Error('TAZ 划分必须包含九个区域')

  const png = await zip.file(expected.od_heatmap_png)!.async('uint8array')
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
  if (png.length <= signature.length || !signature.every((value, index) => png[index] === value)) {
    throw new Error('OD 热力图不是有效 PNG')
  }

  return {
    manifest,
    summary: '导出完成，OD 矩阵、九区 TAZ 和热力图均已校验',
  }
}
