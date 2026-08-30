import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { SceneSwitchCoordinator } from '../src/utils/sceneSwitchCoordinator.ts'
import { VehicleViewportPipeline } from '../src/mapv/vehicleViewportPipeline.ts'
import { useActiveIntersectionScene } from '../src/composables/useActiveIntersectionScene.ts'
import { createIntersectionLanePoseResolver } from '../src/mapv/realistic/intersectionLaneHeading.ts'
import {
  projectBd09ToWebMercator,
  unprojectWebMercatorToBd09,
} from '../src/mapv/sceneCoordinates.ts'

const threeMapSource = await readFile(
  new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
  'utf8',
)
const vehicleRendererSource = await readFile(
  new URL('../src/mapv/BaiduVehicleRenderer.ts', import.meta.url),
  'utf8',
)
const twinPresenterSource = await readFile(
  new URL('../src/mapv/vehicleTwinPresenter.ts', import.meta.url),
  'utf8',
)

function vehicle(id, longitude, laneId = 'lane-a', latitude = 0) {
  return {
    vehicle_id: id,
    longitude,
    latitude,
    x: longitude,
    y: 0,
    speed: 1,
    angle: 90,
    road_id: 'edge-a',
    lane_id: laneId,
    type_id: 'passenger',
    allowed_speed: 10,
  }
}

function frame(sequence, elapsedSeconds, vehicles) {
  return {
    vehicles,
    context: {
      sessionId: 'session-a',
      state: 'PAUSED',
      sequence,
      elapsedSeconds,
      laneRuntimeById: {},
      intersectionId: 'demo_1',
    },
  }
}

function createPoseResolver(resolvePose = true) {
  const resolver = (laneId, coordinate) => {
    if (!resolvePose || laneId !== 'lane-a') return null
    const arcDistanceMeters = coordinate[0] * 111_000
    return {
      longitude: coordinate[0],
      latitude: coordinate[1],
      heading: Math.PI / 2,
      modelCenterResolved: true,
      trackKey: 'track-a',
      motionPathKey: 'path-a',
      segmentKey: 'segment-a',
      occupancyKey: 'lane-a',
      trackProgress: 0.5,
      arcDistanceMeters,
      pathArcDistanceMeters: arcDistanceMeters,
      minimumArcDistanceMeters: 0,
      matchConfidence: 1,
      transitionKind: 'same_lane',
      modelCenterDistanceMeters: arcDistanceMeters,
      naturalFrontDistanceMeters: arcDistanceMeters,
      stopClamped: false,
      sourceArcDistanceMeters: arcDistanceMeters,
      sourceLateralOffsetMeters: 0,
      sourceDistanceToLaneCenterMeters: 0,
      mappedArcDistanceMeters: arcDistanceMeters,
      roadMappingErrorMeters: 0,
      laneWidthMeters: 3.2,
      corridorKind: 'lane',
      poseValid: true,
      mappingMode: 'centerline',
    }
  }
  resolver.hasLane = (laneId) => laneId === 'lane-a'
  resolver.covers = (laneId) => laneId === 'lane-a'
  resolver.coversDetailedArea = () => true
  resolver.motionPathSampler = {
    project: (_key, coordinate) => ({
      pathArcDistanceMeters: coordinate[0] * 111_000,
      distanceMeters: 0,
    }),
    sample: (_key, distanceMeters) => ({
      longitude: distanceMeters / 111_000,
      latitude: 0,
      heading: Math.PI / 2,
      pathArcDistanceMeters: distanceMeters,
    }),
    containsVehicle: () => true,
  }
  return resolver
}

function pipeline(resolvePose = true) {
  return new VehicleViewportPipeline({
    intersectionId: 'demo_1',
    sessionId: 'session-a',
    presentationGeneration: 4,
    pipelineGeneration: 7,
    headingResolver: () => Math.PI / 2,
    poseResolver: createPoseResolver(resolvePose),
    projector: ([longitude, latitude]) => [longitude, latitude, 0],
  })
}

test('a newer scene switch cancels stale work and is the only committable transaction', () => {
  const coordinator = new SceneSwitchCoordinator()
  const first = coordinator.begin('demo_4')
  const second = coordinator.begin('demo_8')

  assert.equal(first.signal.aborted, true)
  assert.equal(coordinator.isCurrent(first), false)
  assert.equal(coordinator.complete(first), false)
  assert.equal(coordinator.isCurrent(second), true)
  assert.equal(coordinator.complete(second), true)
})

test('cancelling a switch prevents any later commit', () => {
  const coordinator = new SceneSwitchCoordinator()
  const transaction = coordinator.begin('demo_6')
  coordinator.cancel()

  assert.equal(transaction.signal.aborted, true)
  assert.equal(coordinator.complete(transaction), false)
})

test('prepares a vehicle stage before camera flight and commits before road activation', () => {
  const prepareIndex = threeMapSource.indexOf('waitForViewportVehicleStage(')
  const beginIndex = threeMapSource.indexOf('vehicleRenderer?.beginViewportTransition(vehicleStage)', prepareIndex)
  const warmIndex = threeMapSource.indexOf('waitForViewportTransitionReady', beginIndex)
  const commitIndex = threeMapSource.indexOf('vehicleRenderer?.commitViewportTransition(', beginIndex)
  const activateIndex = threeMapSource.indexOf('realisticIntersectionLayer.activate(intersectionId)', commitIndex)
  assert.ok(prepareIndex >= 0 && prepareIndex < beginIndex)
  assert.ok(beginIndex < warmIndex && warmIndex < commitIndex && commitIndex < activateIndex)
  assert.match(vehicleRendererSource, /VIEWPORT_SNAPSHOT_HISTORY_SECONDS = 30/)
  assert.match(vehicleRendererSource, /selectViewportReplayVehicles/)
  assert.match(vehicleRendererSource, /stage\.authoritativeLocalVehicleCount > 0/)
  assert.match(vehicleRendererSource, /this\.twinPresenter\.beginReplacement\(samples\)/)
  assert.match(vehicleRendererSource, /this\.twinPresenter\.activateReplacement\(\)/)
  assert.match(twinPresenterSource, /buffers\?\.id/)
  assert.match(twinPresenterSource, /replacement\.actualVisibleCount === 0/)
  assert.doesNotMatch(vehicleRendererSource, /pendingViewportTwinReplacement/)
})

test('a paused latest authoritative endpoint is immediately stage-ready', () => {
  const target = pipeline()
  target.ingest([frame(10, 120, [vehicle('vehicle-1', 0.001)])])
  const stage = target.prepare(120, performance.now())

  assert.equal(stage?.readiness.status, 'ready')
  assert.equal(stage?.sourceVehicleCount, 1)
  assert.equal(stage?.viewportVehicleCount, 1)
  assert.equal(stage?.selectedVehicleCount, 1)
  assert.equal(stage?.authoritativeLocalVehicleCount, 1)
  assert.equal(stage?.firstFrameVehicleCount, 1)
  target.destroy()
})

test('a non-empty authoritative roster with no mapped pose is unresolved', () => {
  const target = pipeline(false)
  target.ingest([frame(11, 121, [vehicle('vehicle-unmapped', 0.002)])])
  const stage = target.prepare(121, performance.now())

  assert.equal(stage?.readiness.status, 'unresolved')
  assert.equal(stage?.authoritativeLocalVehicleCount, 1)
  assert.deepEqual(stage?.readiness.rejectionReasons, ['pose_unmapped'])
  target.destroy()
})

test('nearby unsupported lanes do not block an intersection switch', () => {
  const target = pipeline()
  target.ingest([frame(12, 122, [
    vehicle('nearby-other-intersection', 0.002, 'lane-outside-manifest'),
  ])])
  const stage = target.prepare(122, performance.now())

  assert.equal(stage?.readiness.status, 'viewport_empty')
  assert.equal(stage?.sourceVehicleCount, 1)
  assert.equal(stage?.viewportVehicleCount, 0)
  assert.equal(stage?.authoritativeLocalVehicleCount, 0)
  target.destroy()
})

test('viewport history is ingested incrementally and interpolates a legal first frame', () => {
  const target = pipeline()
  const first = frame(20, 200, [vehicle('vehicle-moving', 0.001)])
  const second = frame(21, 200.5, [vehicle('vehicle-moving', 0.001004)])
  target.ingest([first, second])
  target.ingest([first, second])
  const stage = target.prepare(200.25, performance.now())

  assert.equal(stage?.readiness.status, 'ready')
  assert.equal(stage?.snapshots.length, 2)
  assert.equal(stage?.firstFrameVehicleCount, 1)
  target.destroy()
})

test('all 20 intersection manifests can prepare a non-empty authoritative endpoint', async () => {
  for (let index = 1; index <= 20; index += 1) {
    const intersectionId = `demo_${index}`
    const manifest = JSON.parse(await readFile(
      new URL(`../public/intersections/v3/${intersectionId}/manifest.json`, import.meta.url),
      'utf8',
    ))
    const lane = manifest.edges
      .flatMap((edge) => edge.lanes)
      .find((candidate) => (
        (candidate.kind ?? 'driving') === 'driving'
        && candidate.vehicleGuidePoints?.length >= 2
      ))
    assert.ok(lane, `${intersectionId} needs a driving vehicle guide`)
    const local = lane.vehicleGuidePoints[Math.floor(lane.vehicleGuidePoints.length / 2)]
    const originPlane = projectBd09ToWebMercator([
      manifest.origin.longitude,
      manifest.origin.latitude,
    ])
    const coordinate = unprojectWebMercatorToBd09([
      originPlane[0] + local[0],
      originPlane[1] + local[1],
    ])
    const poseResolver = createIntersectionLanePoseResolver(
      manifest,
      ([longitude, latitude]) => [longitude, latitude, 0],
    )
    const target = new VehicleViewportPipeline({
      intersectionId,
      sessionId: 'session-all-intersections',
      presentationGeneration: 2,
      pipelineGeneration: index,
      headingResolver: () => 0,
      poseResolver,
      projector: ([longitude, latitude]) => [longitude, latitude, 0],
    })
    target.ingest([{
      vehicles: [vehicle(`${intersectionId}-vehicle`, coordinate[0], lane.id, coordinate[1])],
      context: {
        sessionId: 'session-all-intersections',
        state: 'PAUSED',
        sequence: index,
        elapsedSeconds: 300,
        laneRuntimeById: {},
        intersectionId,
      },
    }])
    const stage = target.prepare(300, performance.now())
    assert.equal(stage?.readiness.status, 'ready', intersectionId)
    assert.ok(stage.firstFrameVehicleCount > 0, intersectionId)
    target.destroy()
  }
})

test('Twin replacement keeps the active channel until the warming channel actually ticks', () => {
  const beginIndex = twinPresenterSource.indexOf('beginReplacement(')
  const waitIndex = twinPresenterSource.indexOf('waitForReplacementReady(', beginIndex)
  const activateIndex = twinPresenterSource.indexOf('activateReplacement()', waitIndex)
  const visibleGateIndex = twinPresenterSource.indexOf('replacement.actualVisibleCount === 0', activateIndex)
  const disposeOldIndex = twinPresenterSource.indexOf('this.disposeChannel(previous)', visibleGateIndex)
  assert.ok(beginIndex >= 0 && beginIndex < waitIndex)
  assert.ok(waitIndex < activateIndex && activateIndex < visibleGateIndex)
  assert.ok(visibleGateIndex < disposeOldIndex)
  assert.match(twinPresenterSource, /channel\.freezeWhenVisible = true/)
  assert.match(twinPresenterSource, /channel\.twin\.pause\(\)/)
})

test('failed requested intersection restores the committed selection', () => {
  const scene = useActiveIntersectionScene()
  scene.setSceneReady('demo_3')
  scene.selectIntersection('demo_9')
  assert.equal(scene.selectionState.value.requestedIntersectionId, 'demo_9')
  assert.equal(scene.selectionState.value.committedIntersectionId, 'demo_3')
  assert.equal(scene.selectionState.value.switching, true)

  assert.equal(scene.restoreCommittedIntersection('stage unresolved'), 'demo_3')
  assert.equal(scene.activeIntersectionId.value, 'demo_3')
  assert.equal(scene.sceneStatus.value, 'ready')
  assert.equal(scene.sceneError.value, 'stage unresolved')
})

test('3D hydrates authoritative history instead of pairing latest traffic with a delayed clock', () => {
  assert.match(threeMapSource, /vehicleAuthoritativeHistoryRevision/)
  assert.match(threeMapSource, /getVehicleAuthoritativeHistoryWindow/)
  assert.match(threeMapSource, /function syncVehicleAuthoritativeHistory/)
  assert.doesNotMatch(
    threeMapSource,
    /watch\(\s*trafficView,[\s\S]{0,700}vehicleRenderer\?\.update/,
  )
})

test('a compiling viewport cannot reset Twin as an authoritative empty roster', () => {
  assert.match(vehicleRendererSource, /sampleResult\(/)
  assert.match(vehicleRendererSource, /result\.status === 'waiting'/)
  assert.match(vehicleRendererSource, /result\.status === 'authoritative_empty'/)
  assert.match(vehicleRendererSource, /setCompilationReadyListener/)
  assert.match(vehicleRendererSource, /hydrateAuthoritativeHistory/)
  assert.match(vehicleRendererSource, /if \(!this\.hydrateAuthoritativeHistory\(this\.sharedDisplayElapsedSeconds\)\)/)
})
