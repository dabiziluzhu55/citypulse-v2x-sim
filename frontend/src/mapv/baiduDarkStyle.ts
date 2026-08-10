export const BAIDU_DARK_BASE_STYLE = [
  ['land', 'geometry', '#122e2bff', 'on'],
  ['water', 'geometry', '#0a2551ff', 'on'],
  ['green', 'geometry', '#1a4443ff', 'on'],
  ['manmade', 'geometry', '#142825ff', 'on'],
  ['road', 'geometry', '#202c46ff', 'on'],
  ['road', 'geometry.stroke', '#394a67ff', 'on'],
  ['cityhighway', 'geometry', '#546180ff', 'on'],
  ['cityhighway', 'geometry.stroke', '#687694ff', 'on'],
  ['nationalway', 'geometry', '#2e4253ff', 'on'],
  ['provincialway', 'geometry', '#293b4aff', 'on'],
  ['tertiaryway', 'geometry', '#253542ff', 'on'],
  ['fourlevelway', 'geometry', '#202e39ff', 'on'],
  ['building', 'geometry', '#263746ff', 'off'],
  ['all', 'labels', '#00000000', 'off'],
  ['poilabel', 'all', '#00000000', 'off'],
].map(([featureType, elementType, color, visibility]) => ({
  featureType,
  elementType,
  stylers: { color, visibility },
}))

export function createBaiduBaseDisplayOptions(showRoads: boolean) {
  return {
    base: true,
    link: showRoads,
    building: false,
    poi: false,
    flat: true,
  } as const
}
