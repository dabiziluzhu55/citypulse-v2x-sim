import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendDirectory = path.resolve(scriptsDirectory, '..')
const projectDirectory = path.resolve(frontendDirectory, '..')
const helperPath = path.resolve(scriptsDirectory, 'generate-topology-routes.py')
const argumentsForHelper = [
  helperPath,
  path.resolve(projectDirectory, 'data/maps/sumo/generated/network/TotalMap_20.signals.net.xml'),
  path.resolve(projectDirectory, 'data/maps/sumo/official/map/TotalMap_20.intersections.json'),
  path.resolve(frontendDirectory, 'public/intersections/v3/catalog.json'),
  path.resolve(frontendDirectory, 'public/intersections/v3/topology-routes.json'),
]
const commands = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']
const failures = []

for (const command of commands) {
  const result = spawnSync(command, argumentsForHelper, {
    cwd: frontendDirectory,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 16 * 1024 * 1024,
  })
  if (result.status === 0) {
    process.stdout.write(result.stdout)
    process.exit(0)
  }
  failures.push(`${command}: ${result.stderr?.trim() || result.error?.message || `exit ${result.status}`}`)
}

throw new Error(`Topology route generation failed:\n${failures.join('\n')}`)
