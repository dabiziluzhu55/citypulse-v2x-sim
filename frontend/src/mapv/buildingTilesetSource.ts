export interface BuildingTilesetManifest {
  source?: string
  source_crs?: string
  coordinate_system?: string
  source_tile_count?: number
  output_tile_count?: number
  vertex_count?: number
  triangle_count?: number
  center_wgs84?: [number, number]
  radius_m?: number
}

export interface GlobalBuildingSourceDiagnosis {
  kind: 'global' | 'focused' | 'invalid'
  sourceTiles: number
  outputTiles: number
  vertexCount: number | null
  triangleCount: number | null
}

function finiteCount(value: unknown): number | null {
  return Number.isFinite(value) && Number(value) >= 0 ? Number(value) : null
}

export function buildingTilesetManifestUrl(tilesetUrl: string, baseUrl: string): URL {
  return new URL('./manifest.json', new URL(tilesetUrl, baseUrl))
}

export function diagnoseGlobalBuildingSource(value: unknown): GlobalBuildingSourceDiagnosis {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {
      kind: 'invalid',
      sourceTiles: 0,
      outputTiles: 0,
      vertexCount: null,
      triangleCount: null,
    }
  }
  const manifest = value as BuildingTilesetManifest
  const sourceTiles = finiteCount(manifest.source_tile_count)
  const outputTiles = finiteCount(manifest.output_tile_count)
  const vertexCount = finiteCount(manifest.vertex_count)
  const triangleCount = finiteCount(manifest.triangle_count)
  if (sourceTiles == null || outputTiles == null || outputTiles === 0) {
    return {
      kind: 'invalid',
      sourceTiles: sourceTiles ?? 0,
      outputTiles: outputTiles ?? 0,
      vertexCount,
      triangleCount,
    }
  }
  const focused = Array.isArray(manifest.center_wgs84) || finiteCount(manifest.radius_m) != null
  return {
    kind: focused ? 'focused' : 'global',
    sourceTiles,
    outputTiles,
    vertexCount,
    triangleCount,
  }
}

export function assertGlobalBuildingSource(value: unknown): GlobalBuildingSourceDiagnosis {
  const diagnosis = diagnoseGlobalBuildingSource(value)
  if (diagnosis.kind === 'focused') {
    throw new Error('20 路口模式不能使用单路口裁剪建筑源，请改用全域 3D Tiles')
  }
  if (diagnosis.kind !== 'global') {
    throw new Error('全域建筑 manifest 无效或不包含可加载瓦片')
  }
  return diagnosis
}
