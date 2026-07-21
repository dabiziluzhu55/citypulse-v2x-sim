export const BAIDU_DARK_BASE_STYLE = [
  ['land', 'geometry', '#091220ff', 'on'],
  ['water', 'geometry', '#113549ff', 'on'],
  ['green', 'geometry', '#0e1b30ff', 'on'],
  ['manmade', 'geometry', '#0d1828ff', 'on'],
  ['road', 'geometry', '#263142ff', 'on'],
  ['road', 'geometry.stroke', '#35465bff', 'on'],
  ['building', 'geometry', '#09122000', 'off'],
  ['all', 'labels', '#00000000', 'off'],
  ['poilabel', 'all', '#00000000', 'off'],
].map(([featureType, elementType, color, visibility]) => ({
  featureType,
  elementType,
  stylers: { color, visibility },
}))
