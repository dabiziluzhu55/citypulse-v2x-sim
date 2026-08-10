import * as THREE from 'three'

export const ROAD_ASPHALT_COLOR = 0x515454
export const ROAD_ASPHALT_CSS = '#515454'
export const ROAD_SECONDARY_ASPHALT_CSS = '#565959'
export const ROAD_JUNCTION_COLOR = 0x494c4c
export const ROAD_JUNCTION_CSS = '#494c4c'
export const ROAD_SIDEWALK_COLOR = 0x969994
export const ROAD_SIDEWALK_CSS = '#969994'
export const ROAD_CURB_COLOR = 0xc8c8c1
export const ROAD_ASPHALT_ROUGHNESS = 0.94
export const ROAD_ASPHALT_METALNESS = 0.02

function createCanvasTexture(
  width: number,
  height: number,
  draw: (context: CanvasRenderingContext2D) => void,
): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Canvas 2D context is unavailable')
  draw(context)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 16
  texture.minFilter = THREE.LinearMipmapLinearFilter
  texture.magFilter = THREE.LinearFilter
  texture.generateMipmaps = true
  return texture
}

export function createAsphaltTexture(seedValue: number): THREE.CanvasTexture {
  const texture = createCanvasTexture(256, 256, (context) => {
    context.fillStyle = '#444747'
    context.fillRect(0, 0, 256, 256)
    let seed = Math.max(1, Math.floor(seedValue))
    for (let index = 0; index < 5200; index += 1) {
      seed = (seed * 16807) % 2147483647
      const x = seed % 256
      seed = (seed * 16807) % 2147483647
      const y = seed % 256
      seed = (seed * 16807) % 2147483647
      const shade = 46 + (seed % 35)
      context.fillStyle = `rgba(${shade},${shade},${shade},0.32)`
      context.fillRect(x, y, 1, 1)
    }
  })
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(0.65, 0.65)
  return texture
}

export function createAsphaltMaterial(
  seedValue: number,
  color = ROAD_ASPHALT_COLOR,
): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color,
    map: createAsphaltTexture(seedValue),
    roughness: ROAD_ASPHALT_ROUGHNESS,
    metalness: ROAD_ASPHALT_METALNESS,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  })
}
