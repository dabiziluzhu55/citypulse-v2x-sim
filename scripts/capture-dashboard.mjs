import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const outDir = path.resolve(__dirname, '../assets/preview')
const url = 'http://localhost:5173/'

await mkdir(outDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })

await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 })
await page.waitForTimeout(4000)

const fullPath = path.join(outDir, 'dashboard-full.png')
await page.screenshot({ path: fullPath, fullPage: false })

const bottomPath = path.join(outDir, 'dashboard-bottom.png')
await page.screenshot({
  path: bottomPath,
  clip: { x: 0, y: 900, width: 1920, height: 180 },
})

console.log('Saved:', fullPath)
console.log('Saved:', bottomPath)

await browser.close()
