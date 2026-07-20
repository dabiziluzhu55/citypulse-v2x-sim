const X_PI = Math.PI * 3000.0 / 180.0
const GCJ_A = 6378245.0
const GCJ_EE = 0.00669342162296594323

export const XIONGAN_TILESET_ROOT_CENTER: [number, number] = [
  115.95498986829843,
  38.986485772313685,
]

export const XIONGAN_SCENE_ANCHOR: [number, number] = [115.981, 38.985]

export const DEMO_2_SOURCE_CENTER: [number, number] = [116.126756, 38.99115]

function transformLat(longitude: number, latitude: number): number {
  let result = -100.0 + 2.0 * longitude + 3.0 * latitude
  result += 0.2 * latitude * latitude + 0.1 * longitude * latitude
  result += 0.2 * Math.sqrt(Math.abs(longitude))
  result += (20.0 * Math.sin(6.0 * longitude * Math.PI) + 20.0 * Math.sin(2.0 * longitude * Math.PI)) * 2.0 / 3.0
  result += (20.0 * Math.sin(latitude * Math.PI) + 40.0 * Math.sin(latitude / 3.0 * Math.PI)) * 2.0 / 3.0
  result += (160.0 * Math.sin(latitude / 12.0 * Math.PI) + 320 * Math.sin(latitude * Math.PI / 30.0)) * 2.0 / 3.0
  return result
}

function transformLon(longitude: number, latitude: number): number {
  let result = 300.0 + longitude + 2.0 * latitude
  result += 0.1 * longitude * longitude + 0.1 * longitude * latitude
  result += 0.1 * Math.sqrt(Math.abs(longitude))
  result += (20.0 * Math.sin(6.0 * longitude * Math.PI) + 20.0 * Math.sin(2.0 * longitude * Math.PI)) * 2.0 / 3.0
  result += (20.0 * Math.sin(longitude * Math.PI) + 40.0 * Math.sin(longitude / 3.0 * Math.PI)) * 2.0 / 3.0
  result += (150.0 * Math.sin(longitude / 12.0 * Math.PI) + 300.0 * Math.sin(longitude / 30.0 * Math.PI)) * 2.0 / 3.0
  return result
}

export function wgs84ToGcj02(longitude: number, latitude: number): [number, number] {
  const dLat = transformLat(longitude - 105.0, latitude - 35.0)
  const dLon = transformLon(longitude - 105.0, latitude - 35.0)
  const radLat = latitude / 180.0 * Math.PI
  let magic = Math.sin(radLat)
  magic = 1 - GCJ_EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  const mgLat = latitude + (dLat * 180.0) / ((GCJ_A * (1 - GCJ_EE)) / (magic * sqrtMagic) * Math.PI)
  const mgLon = longitude + (dLon * 180.0) / (GCJ_A / sqrtMagic * Math.cos(radLat) * Math.PI)
  return [mgLon, mgLat]
}

export function gcj02ToBd09(longitude: number, latitude: number): [number, number] {
  const z = Math.sqrt(longitude * longitude + latitude * latitude) + 0.00002 * Math.sin(latitude * X_PI)
  const theta = Math.atan2(latitude, longitude) + 0.000003 * Math.cos(longitude * X_PI)
  return [z * Math.cos(theta) + 0.0065, z * Math.sin(theta) + 0.006]
}

export function wgs84ToBd09(longitude: number, latitude: number): [number, number] {
  return gcj02ToBd09(...wgs84ToGcj02(longitude, latitude))
}

export function projectSimulationCoordinateToXiongan(
  coordinate: readonly number[],
): [number, number, number?] {
  const [longitude, latitude, height] = coordinate
  return [
    XIONGAN_SCENE_ANCHOR[0] + longitude - DEMO_2_SOURCE_CENTER[0],
    XIONGAN_SCENE_ANCHOR[1] + latitude - DEMO_2_SOURCE_CENTER[1],
    height,
  ]
}

export function projectSimulationCoordinateToBaiduMap(
  coordinate: readonly number[],
): [number, number, number?] {
  const [longitude, latitude, height] = coordinate
  const [bdLon, bdLat] = wgs84ToBd09(longitude, latitude)
  return [bdLon, bdLat, height]
}

export function projectSimulationCoordinateToBaiduXiongan(
  coordinate: readonly number[],
): [number, number, number?] {
  const [longitude, latitude, height] = projectSimulationCoordinateToXiongan(coordinate)
  const [bdLon, bdLat] = wgs84ToBd09(longitude, latitude)
  return [bdLon, bdLat, height]
}

export const XIONGAN_SCENE_ANCHOR_BD09: [number, number] = wgs84ToBd09(
  XIONGAN_SCENE_ANCHOR[0],
  XIONGAN_SCENE_ANCHOR[1],
)

export const DEMO_2_SOURCE_CENTER_BD09: [number, number] = wgs84ToBd09(
  DEMO_2_SOURCE_CENTER[0],
  DEMO_2_SOURCE_CENTER[1],
)
