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

async function archive(options = {}) {
  const preset = options.preset ?? 'xiongan_20'
  const zip = new JSZip()
  const manifest = {
    scenario_preset_id: preset,
    od_included: preset === 'xiongan_20',
    files: preset === 'xiongan_20' ? EXPECTED_FILES : {},
    ...(options.manifest ?? {}),
  }
  zip.file('export_manifest.json', JSON.stringify(manifest))
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
  })
  assert.equal(result.manifest.od_included, true)
  assert.match(result.summary, /OD 矩阵、九区 TAZ 和热力图/)
})

test('rejects missing OD files, invalid TAZ counts, and manifest path mismatches', async () => {
  await assert.rejects(
    validateScenarioArchive(await archive({ remove: EXPECTED_FILES.od_heatmap_png }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD,
    }),
    /缺少 od\/od_heatmap/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ zoneCount: 8 }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD,
    }),
    /九个区域/,
  )
  await assert.rejects(
    validateScenarioArchive(await archive({ manifest: { files: { ...EXPECTED_FILES, od_taz_json: 'wrong.json' } } }), {
      scenarioPresetId: 'xiongan_20', period: PERIOD,
    }),
    /路径不匹配/,
  )
})

test('requires local dense presets to omit global OD artifacts', async () => {
  const result = await validateScenarioArchive(await archive({ preset: 'east_dense' }), {
    scenarioPresetId: 'east_dense', period: PERIOD,
  })
  assert.equal(result.manifest.od_included, false)
  await assert.rejects(
    validateScenarioArchive(await archive({ preset: 'west_dense', addOd: true }), {
      scenarioPresetId: 'west_dense', period: PERIOD,
    }),
    /不应包含全域 OD/,
  )
})
