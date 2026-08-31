import type { SimulationLaneRuntime, SimulationState } from '../types/simulation'
import type { TrafficVehicleView } from '../types/traffic'
import type { RoadCoordinateProjector } from './roadGeometry'
import type {
  LaneHeadingResolver,
  LanePoseResolver,
} from './realistic/intersectionLaneHeading'
import { resolveVehicleModelProfile } from './vehicleModelProfiles.ts'
import {
  VehicleMotionBuffer,
  type VehicleMotionSampleResult,
} from './vehicleMotionBuffer.ts'
import { createVehicleTwinSample, type VehicleTwinSample } from './vehicleTwinSample.ts'

export interface VehicleViewportFrameContext {
  sessionId: string
  state: SimulationState | null
  sequence: number
  elapsedSeconds: number
  laneRuntimeById: Record<string, SimulationLaneRuntime>
  trafficPeriod?: string
  intersectionId: string
  playbackSpeed?: number
}

export interface VehicleViewportAuthoritativeFrame {
  vehicles: TrafficVehicleView[]
  context: VehicleViewportFrameContext
}

export type ViewportStageReadiness =
  | { status: 'waiting'; reason: string }
  | { status: 'ready'; sampleCount: number }
  | { status: 'authoritative_empty'; sampleCount: 0 }
  | { status: 'viewport_empty'; sampleCount: 0 }
  | { status: 'selection_empty'; sampleCount: 0 }
  | { status: 'unresolved'; reason: string; rejectionReasons: string[] }

export interface PreparedViewportVehicleStage {
  intersectionId: string
  sessionId: string
  presentationGeneration: number
  pipelineGeneration: number
  snapshots: VehicleViewportAuthoritativeFrame[]
  headingResolver: LaneHeadingResolver | null
  poseResolver: LanePoseResolver | null
  displayElapsedSeconds: number
  sourceVehicleCount: number
  viewportVehicleCount: number
  selectedVehicleCount: number
  authoritativeLocalVehicleCount: number
  precompileMilliseconds: number
  firstFrameVehicleCount: number
  firstFrameSamples: VehicleTwinSample[]
  priorityVehicleIds: string[]
  readiness: ViewportStageReadiness
}

interface VehicleViewportPipelineOptions {
  intersectionId: string
  sessionId: string
  presentationGeneration: number
  pipelineGeneration: number
  headingResolver: LaneHeadingResolver | null
  poseResolver: LanePoseResolver | null
  projector: RoadCoordinateProjector
}

export class VehicleViewportPipeline {
  private readonly motionBuffer = new VehicleMotionBuffer()
  private readonly snapshotsBySequence = new Map<number, VehicleViewportAuthoritativeFrame>()
  private readonly processedSequences = new Set<number>()
  private readonly readyListeners = new Set<() => void>()
  private disposed = false

  readonly intersectionId: string
  readonly sessionId: string
  readonly presentationGeneration: number
  readonly pipelineGeneration: number
  readonly headingResolver: LaneHeadingResolver | null
  readonly poseResolver: LanePoseResolver | null
  private readonly projector: RoadCoordinateProjector

  constructor(options: VehicleViewportPipelineOptions) {
    this.intersectionId = options.intersectionId
    this.sessionId = options.sessionId
    this.presentationGeneration = options.presentationGeneration
    this.pipelineGeneration = options.pipelineGeneration
    this.headingResolver = options.headingResolver
    this.poseResolver = options.poseResolver
    this.projector = options.projector
    this.motionBuffer.setMotionPathSampler(options.poseResolver?.motionPathSampler ?? null)
    this.motionBuffer.setCompilationReadyListener(() => {
      if (this.disposed) return
      for (const listener of this.readyListeners) listener()
    })
  }

  ingest(frames: readonly VehicleViewportAuthoritativeFrame[]): void {
    if (this.disposed) return
    const ordered = [...frames]
      .filter((frame) => (
        frame.context.sessionId === this.sessionId
        && frame.context.intersectionId === this.intersectionId
      ))
      .sort((left, right) => (
        left.context.elapsedSeconds - right.context.elapsedSeconds
        || left.context.sequence - right.context.sequence
      ))
    for (const frame of ordered) {
      this.snapshotsBySequence.set(frame.context.sequence, frame)
      if (this.processedSequences.has(frame.context.sequence)) continue
      this.processedSequences.add(frame.context.sequence)
      const compiled = this.compileAuthoritativeFrame(frame)
      this.motionBuffer.push({
        sceneGeneration: this.pipelineGeneration,
        sequence: frame.context.sequence,
        elapsedSeconds: frame.context.elapsedSeconds,
        arrivalTimeMs: performance.now(),
        samples: compiled.samples,
        sourceVehicleIds: compiled.sourceVehicleIds,
        viewportVehicleIds: compiled.viewportVehicleIds,
        selectedVehicleIds: compiled.selectedVehicleIds,
      })
    }
  }

  sample(displayElapsedSeconds: number): VehicleMotionSampleResult {
    return this.motionBuffer.sampleResult(performance.now(), displayElapsedSeconds)
  }

  prepare(displayElapsedSeconds: number, startedAt: number): PreparedViewportVehicleStage | null {
    const result = this.sample(displayElapsedSeconds)
    const snapshots = [...this.snapshotsBySequence.values()].sort((left, right) => (
      left.context.elapsedSeconds - right.context.elapsedSeconds
      || left.context.sequence - right.context.sequence
    ))
    const authoritativeLocalVehicleCount = result.viewportVehicleCount
    if (result.status === 'waiting') return null
    const firstFrameSamples = result.status === 'ready' ? [...result.samples] : []
    const readiness: ViewportStageReadiness = result.status === 'ready'
      ? { status: 'ready', sampleCount: result.samples.length }
      : result.status === 'authoritative_empty'
        ? { status: 'authoritative_empty', sampleCount: 0 }
        : result.status === 'viewport_empty'
          ? { status: 'viewport_empty', sampleCount: 0 }
          : result.status === 'selection_empty'
            ? { status: 'selection_empty', sampleCount: 0 }
        : {
            status: 'unresolved',
            reason: result.reason,
            rejectionReasons: [...result.rejectionReasons],
          }
    return {
      intersectionId: this.intersectionId,
      sessionId: this.sessionId,
      presentationGeneration: this.presentationGeneration,
      pipelineGeneration: this.pipelineGeneration,
      snapshots,
      headingResolver: this.headingResolver,
      poseResolver: this.poseResolver,
      displayElapsedSeconds,
      sourceVehicleCount: result.sourceVehicleCount,
      viewportVehicleCount: result.viewportVehicleCount,
      selectedVehicleCount: result.selectedVehicleCount,
      authoritativeLocalVehicleCount,
      precompileMilliseconds: performance.now() - startedAt,
      firstFrameVehicleCount: firstFrameSamples.length,
      firstFrameSamples,
      priorityVehicleIds: firstFrameSamples.map((sample) => sample.id),
      readiness,
    }
  }

  onCompilationReady(listener: () => void): () => void {
    this.readyListeners.add(listener)
    return () => this.readyListeners.delete(listener)
  }

  destroy(): void {
    if (this.disposed) return
    this.disposed = true
    this.readyListeners.clear()
    this.motionBuffer.destroy()
  }

  private compileAuthoritativeFrame(frame: VehicleViewportAuthoritativeFrame): {
    samples: VehicleTwinSample[]
    sourceVehicleIds: string[]
    viewportVehicleIds: string[]
    selectedVehicleIds: string[]
  } {
    const samples: VehicleTwinSample[] = []
    const sourceVehicleIds = [...new Set(frame.vehicles.map((vehicle) => vehicle.vehicle_id))]
    const viewportVehicleIds: string[] = []
    const selectedVehicleIds: string[] = []
    const resolver = this.poseResolver
    if (!resolver) return { samples, sourceVehicleIds, viewportVehicleIds, selectedVehicleIds }
    for (const vehicle of frame.vehicles) {
      if (vehicle.longitude == null || vehicle.latitude == null) continue
      const coordinate = this.projector([vehicle.longitude, vehicle.latitude, 0])
      const mapCoordinate: [number, number] = [coordinate[0], coordinate[1]]
      // Nearby roads can fall inside the detailed-area polygon without being
      // part of this intersection manifest. Do not classify those vehicles as
      // authoritative viewport vehicles, otherwise staging becomes
      // `selection_empty` and the scene switch rolls back.
      if (!resolver.hasLane(vehicle.lane_id)) continue
      const belongsToViewport = resolver.coversDetailedArea(mapCoordinate)
        || resolver.covers(vehicle.lane_id, mapCoordinate)
      if (!belongsToViewport) continue
      viewportVehicleIds.push(vehicle.vehicle_id)
      selectedVehicleIds.push(vehicle.vehicle_id)
      const profile = resolveVehicleModelProfile(vehicle.type_id)
      const pose = resolver(
        vehicle.lane_id,
        mapCoordinate,
        profile.targetLengthMeters / 2,
      )
      if (!pose || !pose.poseValid) continue
      const frontToCenterOffsetMeters = pose.modelCenterResolved === false
        ? profile.targetLengthMeters / 2
        : 0
      samples.push({
        ...createVehicleTwinSample(
          vehicle,
          pose.longitude,
          pose.latitude,
          frame.context.elapsedSeconds * 1_000,
          profile,
          pose.heading,
          pose.modelCenterResolved !== false,
          {
            motionPathKey: pose.motionPathKey,
            segmentKey: pose.segmentKey,
            occupancyKey: pose.occupancyKey,
            corridorMotionPathKeys: [pose.motionPathKey],
            detailedCorridorValidation: true,
            arcDistanceMeters: pose.arcDistanceMeters - frontToCenterOffsetMeters,
            pathArcDistanceMeters: pose.pathArcDistanceMeters - frontToCenterOffsetMeters,
            sourceArcDistanceMeters: pose.sourceArcDistanceMeters == null
              ? undefined
              : pose.sourceArcDistanceMeters - frontToCenterOffsetMeters,
            sourceLateralOffsetMeters: pose.sourceLateralOffsetMeters,
            transitionKind: pose.transitionKind,
            roadTransitionKind: 'same_path',
            poseSource: 'topology',
            dynamicConnectionEvidence: {
              source: 'unresolved',
              observedLaneId: vehicle.lane_id,
            },
          },
        ),
        sceneGeneration: this.pipelineGeneration,
        motionEpoch: 0,
      })
    }
    return {
      samples,
      sourceVehicleIds,
      viewportVehicleIds: [...new Set(viewportVehicleIds)],
      selectedVehicleIds: [...new Set(selectedVehicleIds)],
    }
  }
}
