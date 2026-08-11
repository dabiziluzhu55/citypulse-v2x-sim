export interface FullRoadNetworkMetadata {
  schemaVersion: 1
  source: string
  sourceSha256: string
  edgeCount: number
  bounds: {
    west: number
    south: number
    east: number
    north: number
  }
}

export interface FullRoadNetworkGeoJson {
  type: 'FeatureCollection'
  metadata: FullRoadNetworkMetadata
  features: Array<{
    type: 'Feature'
    properties: {
      edge_id: string
      from_junction: string
      to_junction: string
      lane_count: number
    }
    geometry: {
      type: 'LineString'
      coordinates: number[][]
    }
  }>
}

let request: Promise<FullRoadNetworkGeoJson> | null = null

export function loadFullRoadNetwork(
  url = '/intersections/v3/full-road-network.geojson',
): Promise<FullRoadNetworkGeoJson> {
  request ??= fetch(url).then(async (response) => {
    if (!response.ok) throw new Error(`完整路网加载失败：HTTP ${response.status}`)
    const value = await response.json() as FullRoadNetworkGeoJson
    if (
      value.type !== 'FeatureCollection'
      || value.metadata?.schemaVersion !== 1
      || value.metadata.edgeCount !== value.features?.length
    ) throw new Error('完整路网资源格式无效')
    return value
  }).catch((cause) => {
    request = null
    throw cause
  })
  return request
}

export function releaseFullRoadNetwork(): void {
  request = null
}
