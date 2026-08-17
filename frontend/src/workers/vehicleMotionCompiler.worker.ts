import {
  compileMotionSegment,
  type CompiledMotionSegment,
  type TimedVehicleSample,
} from '../mapv/vehicleMotionBuffer'
import {
  createMotionPathSamplerFromWorkerGeometry,
  type MotionPathSampler,
  type MotionPathWorkerGeometry,
} from '../mapv/realistic/intersectionLaneHeading'

interface ConfigureMessage {
  type: 'configure'
  generation: number
  geometry: MotionPathWorkerGeometry
}

interface CompileMessage {
  type: 'compile'
  generation: number
  jobs: Array<{
    requestId: number
    left: TimedVehicleSample
    right: TimedVehicleSample
  }>
}

interface CompileResult {
  requestId: number
  segment: CompiledMotionSegment
  durationMs: number
}

interface CompileResponse {
  type: 'compiled'
  generation: number
  results: CompileResult[]
}

const workerScope = self as unknown as {
  onmessage: ((event: MessageEvent<ConfigureMessage | CompileMessage>) => void) | null
  postMessage: (message: CompileResponse) => void
}

let configuredGeneration = -1
let sampler: MotionPathSampler | null = null

workerScope.onmessage = (event) => {
  const message = event.data
  if (message.type === 'configure') {
    configuredGeneration = message.generation
    sampler = createMotionPathSamplerFromWorkerGeometry(message.geometry)
    return
  }
  if (message.generation !== configuredGeneration || !sampler) return
  const results = message.jobs.map((job) => {
    const startedAt = performance.now()
    const segment = compileMotionSegment(job.left, job.right, sampler)
    return {
      requestId: job.requestId,
      segment,
      durationMs: performance.now() - startedAt,
    }
  })
  workerScope.postMessage({
    type: 'compiled',
    generation: message.generation,
    results,
  })
}

export {}
