import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const violations = []
async function scan(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name)
    if (entry.isDirectory()) await scan(filename)
    else if (/\.(?:ts|js|vue)$/.test(entry.name)) {
      const source = await readFile(filename, 'utf8')
      // Browser keys must come from runtime config, never literal fallback values.
      if (/(?:AK|KEY|TOKEN)\s*=\s*[^\n]*['"][A-Za-z0-9_-]{24,}['"]/.test(source)) {
        violations.push(path.relative(root, filename))
      }
    }
  }
}
await scan(path.join(root, 'src'))
const template = await readFile(path.join(root, '.env.example'), 'utf8')
if (/^VITE_(?:BAIDU_MAP_AK|TIANDITU_TOKEN|AMAP_MAP_KEY|CARTO_BASEMAP_KEY)=[ \t]*\S+/m.test(template)) {
  violations.push('.env.example')
}
if (violations.length) throw new Error(`Potential committed browser credentials in: ${violations.join(', ')} (values withheld)`)
console.log('PASS: no literal browser-key fallbacks or populated map-key templates')
