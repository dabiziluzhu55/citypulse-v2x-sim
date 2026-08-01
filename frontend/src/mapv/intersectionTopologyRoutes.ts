export interface IntersectionTopologyRoute {
  routeId: string
  from: string
  to: string
  lengthMeters: number
  coordinates: Array<[number, number]>
}

export interface IntersectionTopologyRouteManifest {
  schemaVersion: 1
  generatedAt: string
  sourceSha256: string
  coordinateSystem: 'WGS84'
  routes: IntersectionTopologyRoute[]
}

function isDemoId(value: unknown): value is string {
  return typeof value === 'string' && /^demo_(?:[1-9]|1\d|20)$/.test(value)
}

function isCoordinate(value: unknown): value is [number, number] {
  return Array.isArray(value)
    && value.length === 2
    && value.every((item) => typeof item === 'number' && Number.isFinite(item))
}

export function parseIntersectionTopologyRoutes(
  value: unknown,
  expectedSourceSha256?: string,
): IntersectionTopologyRouteManifest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Intersection topology routes must be an object')
  }
  const source = value as Partial<IntersectionTopologyRouteManifest>
  if (
    source.schemaVersion !== 1
    || source.coordinateSystem !== 'WGS84'
    || typeof source.sourceSha256 !== 'string'
    || !Array.isArray(source.routes)
  ) throw new Error('Intersection topology routes are incompatible')
  if (expectedSourceSha256 && source.sourceSha256 !== expectedSourceSha256) {
    throw new Error('Intersection topology routes do not match the intersection catalog')
  }
  const routeIds = new Set<string>()
  const routes = source.routes.map((route) => {
    if (
      !route
      || typeof route !== 'object'
      || !isDemoId(route.from)
      || !isDemoId(route.to)
      || typeof route.routeId !== 'string'
      || route.routeId !== [route.from, route.to].join(':')
      || typeof route.lengthMeters !== 'number'
      || !Number.isFinite(route.lengthMeters)
      || route.lengthMeters <= 0
      || !Array.isArray(route.coordinates)
      || route.coordinates.length < 2
      || !route.coordinates.every(isCoordinate)
    ) throw new Error('Intersection topology routes contain an invalid route')
    if (routeIds.has(route.routeId)) throw new Error('Intersection topology routes contain duplicate ids')
    routeIds.add(route.routeId)
    return {
      routeId: route.routeId,
      from: route.from,
      to: route.to,
      lengthMeters: route.lengthMeters,
      coordinates: route.coordinates.map((coordinate) => [...coordinate] as [number, number]),
    }
  })
  return {
    schemaVersion: 1,
    generatedAt: typeof source.generatedAt === 'string' ? source.generatedAt : '',
    sourceSha256: source.sourceSha256,
    coordinateSystem: 'WGS84',
    routes,
  }
}

export async function loadIntersectionTopologyRoutes(
  url = '/intersections/v3/topology-routes.json',
): Promise<IntersectionTopologyRouteManifest> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Intersection topology routes returned HTTP ${response.status}`)
  return parseIntersectionTopologyRoutes(await response.json())
}
