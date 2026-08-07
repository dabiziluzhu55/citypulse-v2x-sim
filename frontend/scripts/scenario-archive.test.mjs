import assert from 'node:assert/strict'
import test from 'node:test'
import JSZip from 'jszip'

import { validateScenarioArchive } from '../src/utils/scenarioArchiveValidation.ts'

const PERIOD = 'morning_peak'
const EXPECTED_FILES = {
  od_matrix_csv: `od/od_matrix_${PERIOD}.csv`,
  od_taz_json: 'od/taz_9_zones.json',
  od_heatmap_png: `od/od_heatmap_${PERIOD}.png`,
}
const BASE_FILES = {
  sumocfg: 'session.sumocfg',
  routes: 'session.rou.xml',
  additional: 'session.add.xml',
  events: 'events.json',
  network: 'TotalMap_20.signals.net.xml',
}

function presetIntersectionIds(preset) {
  if (preset === 'east_dense') return ['demo_3', 'demo_5', 'demo_6', 'demo_9']
  if (preset === 'west_dense') return ['demo_14', 'demo_15', 'demo_19']
  return Array.from({ length: 20 }, (_, index) => `demo_${index + 1}`)
}

async function archive(options = {}) {
  const preset = options.preset ?? 'xiongan_20'
  const zip = new JSZip()
  const manifest = {
    schema_version: 1,
    scenario_preset_id: preset,
    controlled_intersection_ids: presetIntersectionIds(preset),
    period: PERIOD,
    control_mode: 'fixed',
    od_included: preset === 'xiongan_20',
    files: preset === 'xiongan_20'
      ? { ...BASE_FILES, ...EXPECTED_FILES }
      : { ...BASE_FILES },
    ...(options.manifest ?? {}),
  }
  zip.file('export_manifest.json', JSON.stringify(manifest))
  for (const path of Object.values(BASE_FILES)) zip.file(path, '<xml />')
  if (options.removeBase) zip.remove(options.removeBase)
  if (preset === 'xiongan_20') {
    zip.file(EXPECTED_FILES.od_matrix_csv, `origin,${Array.from({ length: 9 }, (_, index) => `zone_${index + 1}`).join(',')}\nzone_1,0,1,2,3,4,5,6,7,8`)
    zip.file(EXPECTED_FILES.od_taz_json, JSON.stringify({ zone_count: options.zoneCount ?? 9 }))
    zip.file(EXPECTED_FILES.od_heatmap_png, Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]))
    if (options.remove) zip.remove(options.remove)
  } else if (options.addOd) {
    zip.file('od/unexpected.csv', 'bad')
  }
  return new Blob([await zip.generateAsync({ type: 'uint8array' })], { type: 'application/zip' })
}

test('accepts a complete 20-intersection archive with OD, TAZ, and PNG artifacts', async () => {
  const result = await validateScenarioArchive(await archive(), {
    scenarioPresetId: 'xiongan_20',
    period: PERIOD,
    controlMode: 'fixed',
  })
  assert.equal(result.manifest.od_included, true)
  assert.match(result.summary, /OD 矩阵、九区 TAZ 和热力图/)
})

test('rejects missing OD files, invalid TAZ counts, and manifest path mismatches', async () => {
  await assert.rejects(
    validateScenarioArchive(await archive({ remove: EXPECTED_FILES.od_heatmap_png }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /缺少 od\/od_heatmap/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ zoneCount: 8 }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /九个区域/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ manifest: { files: { ...BASE_FILES, ...EXPECTED_FILES, od_taz_json: 'wrong.json' } } }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /路径不匹配/,
  )
})

test('requires local dense presets to omit global OD artifacts', async () => {
  const result = await validateScenarioArchive(await archive({ preset: 'east_dense' }), {
    scenarioPresetId: 'east_dense', period: PERIOD,
    controlMode: 'fixed',
  })
  assert.equal(result.manifest.od_included, false)
  await assert.rejects(
    validateScenarioArchive(await archive({ preset: 'west_dense', addOd: true }), {
      scenarioPresetId: 'west_dense', period: PERIOD,
      controlMode: 'fixed',
    }),
    /不应包含全域 OD/,
  )
})

test('rejects wrong schema, algorithm, intersection scope, and missing SUMO files', async () => {
  await assert.rejects(
    validateScenarioArchive(await archive({ manifest: { schema_version: 2 } }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /Schema 不受支持/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ manifest: { control_mode: 'mappo' } }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /管控算法不匹配/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ manifest: { controlled_intersection_ids: ['demo_2'] } }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /路口范围不匹配/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ removeBase: BASE_FILES.network }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD, controlMode: 'fixed',
    }),
    /缺少 TotalMap_20\.signals\.net\.xml/,
  )
})
