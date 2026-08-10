export type VegetationKind = 'tree' | 'hedge' | 'bush' | 'grass' | 'flowers'

export interface SceneVegetationItem {
  id: string
  kind: VegetationKind
  variant: string
  position: [number, number, number?]
  heading: number
  scale: number
  cell: string
}

export interface SceneVegetationManifest {
  schemaVersion: 1
  source: {
    model: string
    license: 'unverified' | 'cleared'
  }
  cellSizeMeters: number
  items: SceneVegetationItem[]
}

const VEGETATION_KINDS = new Set<VegetationKind>([
  'tree',
  'hedge',
  'bush',
  'grass',
  'flowers',
])

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`)
  }
  return value as Record<string, unknown>
}

export function parseSceneVegetationManifest(value: unknown): SceneVegetationManifest {
  const manifest = record(value, 'Vegetation manifest')
  if (manifest.schemaVersion !== 1) throw new Error('Vegetation manifest requires schemaVersion 1')
  if (!Number.isFinite(manifest.cellSizeMeters) || Number(manifest.cellSizeMeters) <= 0) {
    throw new Error('Vegetation manifest cellSizeMeters must be positive')
  }
  const source = record(manifest.source, 'Vegetation manifest source')
  if (!String(source.model ?? '')) throw new Error('Vegetation manifest source.model is required')
  if (source.license !== 'unverified' && source.license !== 'cleared') {
    throw new Error('Vegetation manifest source.license is invalid')
  }
  if (!Array.isArray(manifest.items)) throw new Error('Vegetation manifest items must be an array')

  const ids = new Set<string>()
  for (const [index, candidate] of manifest.items.entries()) {
    const item = record(candidate, `Vegetation item ${index}`)
    const id = String(item.id ?? '')
    if (!id || ids.has(id)) throw new Error(`Vegetation item ${index} has an invalid or duplicate id`)
    ids.add(id)
    if (!VEGETATION_KINDS.has(item.kind as VegetationKind)) {
      throw new Error(`Vegetation item ${index} has an invalid kind`)
    }
    if (!String(item.variant ?? '') || !String(item.cell ?? '')) {
      throw new Error(`Vegetation item ${index} requires variant and cell`)
    }
    if (!Array.isArray(item.position)
      || item.position.length < 2
      || !item.position.every(Number.isFinite)) {
      throw new Error(`Vegetation item ${index} position must contain finite coordinates`)
    }
    if (!Number.isFinite(item.heading) || !Number.isFinite(item.scale) || Number(item.scale) <= 0) {
      throw new Error(`Vegetation item ${index} has an invalid transform`)
    }
  }

  return manifest as unknown as SceneVegetationManifest
}
