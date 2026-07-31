export interface IntersectionTopologyNode {
  intersectionId: string
  longitude: number
  latitude: number
}

export interface IntersectionTopologyLink {
  id: string
  from: IntersectionTopologyNode
  to: IntersectionTopologyNode
  distanceMeters: number
}

interface IntersectionCatalogPayload {
  schemaVersion: number
  intersections: Array<{
    intersectionId: string
    longitude: number
    latitude: number
  }>
}

const EARTH_RADIUS_METERS = 6_371_008.8
const DEFAULT_NEIGHBOR_LIMIT_METERS = 14_000

function isFiniteCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function parseIntersectionTopologyCatalog(value: unknown): IntersectionTopologyNode[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Intersection topology catalog must be an object')
  }
  const source = value as Partial<IntersectionCatalogPayload>
  if (source.schemaVersion !== 3 || !Array.isArray(source.intersections)) {
    throw new Error('Intersection topology catalog is incompatible')
  }
  const nodes = source.intersections.map((entry) => {
    if (
      !entry
      || typeof entry.intersectionId !== 'string'
      || !/^demo_(?:[1-9]|1\d|20)$/.test(entry.intersectionId)
      || !isFiniteCoordinate(entry.longitude)
      || !isFiniteCoordinate(entry.latitude)
    ) {
      throw new Error('Intersection topology catalog contains an invalid node')
    }
    return {
      intersectionId: entry.intersectionId,
      longitude: entry.longitude,
      latitude: entry.latitude,
    }
  })
  if (new Set(nodes.map((node) => node.intersectionId)).size !== nodes.length) {
    throw new Error('Intersection topology catalog contains duplicate ids')
  }
  return nodes.sort((a, b) => a.intersectionId.localeCompare(b.intersectionId, undefined, { numeric: true }))
}

export async function loadIntersectionTopologyCatalog(
  url = '/intersections/v3/catalog.json',
): Promise<IntersectionTopologyNode[]> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Intersection topology catalog returned HTTP ${response.status}`)
  return parseIntersectionTopologyCatalog(await response.json())
}

export function topologyDistanceMeters(
  a: Pick<IntersectionTopologyNode, 'longitude' | 'latitude'>,
  b: Pick<IntersectionTopologyNode, 'longitude' | 'latitude'>,
): number {
  const latitudeA = a.latitude * Math.PI / 180
  const latitudeB = b.latitude * Math.PI / 180
  const latitudeDelta = latitudeB - latitudeA
  const longitudeDelta = (b.longitude - a.longitude) * Math.PI / 180
  const haversine = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(latitudeA) * Math.cos(latitudeB) * Math.sin(longitudeDelta / 2) ** 2
  return 2 * EARTH_RADIUS_METERS * Math.asin(Math.min(1, Math.sqrt(haversine)))
}

export function buildIntersectionTopologyLinks(
  nodes: IntersectionTopologyNode[],
  neighborCount = 2,
  maxNeighborDistanceMeters = DEFAULT_NEIGHBOR_LIMIT_METERS,
): IntersectionTopologyLink[] {
  const links = new Map<string, IntersectionTopologyLink>()
  for (const from of nodes) {
    const candidates = nodes
      .filter((node) => node.intersectionId !== from.intersectionId)
      .map((to) => ({ to, distanceMeters: topologyDistanceMeters(from, to) }))
      .sort((a, b) => a.distanceMeters - b.distanceMeters)
    const local = candidates.filter((candidate) => candidate.distanceMeters <= maxNeighborDistanceMeters)
    const selected = (local.length ? local : candidates).slice(0, Math.max(1, neighborCount))
    for (const { to, distanceMeters } of selected) {
      const ids = [from.intersectionId, to.intersectionId].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
      const id = `${ids[0]}:${ids[1]}`
      if (links.has(id)) continue
      links.set(id, { id, from, to, distanceMeters })
    }
  }
  return [...links.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
}

export function intersectionTopologyBounds(
  nodes: IntersectionTopologyNode[],
  paddingMeters = 2_500,
): [number, number, number, number] | null {
  if (!nodes.length) return null
  const south = Math.min(...nodes.map((node) => node.latitude))
  const north = Math.max(...nodes.map((node) => node.latitude))
  const west = Math.min(...nodes.map((node) => node.longitude))
  const east = Math.max(...nodes.map((node) => node.longitude))
  const centerLatitude = (south + north) / 2
  const latitudePadding = paddingMeters / 110_900
  const longitudePadding = latitudePadding / Math.max(0.2, Math.cos(centerLatitude * Math.PI / 180))
  return [
    west - longitudePadding,
    south - latitudePadding,
    east + longitudePadding,
    north + latitudePadding,
  ]
}
