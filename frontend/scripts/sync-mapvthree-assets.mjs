import { cp, mkdir, rm } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const source = fileURLToPath(
  new URL('../node_modules/@baidumap/mapv-three/dist/assets/', import.meta.url),
)
const targetParent = fileURLToPath(new URL('../public/mapvthree/', import.meta.url))
const target = fileURLToPath(new URL('../public/mapvthree/assets/', import.meta.url))

// Vite scans public/ once while starting. Finish the replacement before Vite starts so
// its in-memory public-file index can never observe a half-copied asset directory.
await rm(target, { recursive: true, force: true })
await mkdir(targetParent, { recursive: true })
await cp(source, target, { recursive: true, force: true })

console.log('mapv-three 静态资源已同步到 public/mapvthree/assets')
