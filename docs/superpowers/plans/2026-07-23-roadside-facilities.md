# Roadside Facilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stable roadside facilities, lane arrows, and simulation-driven traffic lights to the existing Baidu MapV Three scene without changing the backend or duplicating the building tileset.

**Architecture:** Generate one deterministic frontend asset from the checked-in SUMO WGS84 roads and TLS manifest. Render low-poly facilities with the installed Three.js package, project all positions through the existing WGS84-to-BD09 pipeline, and update only traffic-light instance colors from the existing simulation snapshot.

**Tech Stack:** Vue 3, TypeScript, Three.js, MapV Three, Node.js test runner.

---

### Task 1: Define and test the scene facility contract

**Files:**
- Create: `frontend/scripts/roadside-facilities.test.mjs`
- Create: `frontend/src/mapv/showcaseLayers/sceneFacilities.ts`

- [ ] Write a failing test importing `buildSceneFacilityManifest` and `resolveSignalColor`.
- [ ] Assert that identical SUMO/TLS inputs produce identical lamp, camera, signal, and arrow arrays.
- [ ] Assert that `GREEN`, `YELLOW`, and `CLEARANCE` resolve through TLS templates, while missing runtime state resolves to red.
- [ ] Run `node --test scripts/roadside-facilities.test.mjs` and confirm it fails because the module does not exist.
- [ ] Implement the smallest deterministic placement and signal mapping functions.
- [ ] Run the test and confirm it passes.

### Task 2: Generate the static frontend asset

**Files:**
- Create: `frontend/scripts/generate-scene-facilities.mjs`
- Create: `frontend/public/showcase-data/demo_2.facilities.json`
- Modify: `frontend/package.json`

- [ ] Add `generate:scene-facilities` to `package.json`.
- [ ] Read `data/maps/sumo/generated/geojson/demo_2.roads.wgs84.geojson` and `data/maps/sumo/generated/tls_manifest.json`.
- [ ] Reject TLS connections whose `from_edge` is absent from the GeoJSON.
- [ ] Write the deterministic manifest to `frontend/public/showcase-data/demo_2.facilities.json`.
- [ ] Run the generator twice and confirm the output hash is unchanged.

### Task 3: Render facilities with the existing Three.js dependency

**Files:**
- Create: `frontend/src/mapv/showcaseLayers/RoadsideFacilityRenderer.ts`
- Extend test: `frontend/scripts/roadside-facilities.test.mjs`

- [ ] Add a failing renderer test with a minimal fake engine and identity projector.
- [ ] Confirm the test fails because `RoadsideFacilityRenderer` is missing.
- [ ] Add one Three.js `Group` containing instanced poles, lamp heads, cameras, signal housings, three lens groups, and road arrows.
- [ ] Update signal lens instance colors without rebuilding geometry.
- [ ] Dispose every geometry and material on destroy.
- [ ] Run the focused test and confirm it passes.

### Task 4: Wire facilities to the 3D map and simulation snapshot

**Files:**
- Modify: `frontend/src/components/visualization/BaiduThreeMap.vue`
- Modify: `frontend/src/types/traffic.ts`
- Modify: `frontend/src/utils/trafficStateMerge.ts`
- Modify: `frontend/src/vite-env.d.ts`
- Modify: `frontend/.env.example`

- [ ] Preserve `stage` in `TrafficIntersectionView` so TLS colors match the backend snapshot contract.
- [ ] Create the facility renderer only when `VITE_ENABLE_ROADSIDE_FACILITIES=true`.
- [ ] Load `/showcase-data/demo_2.facilities.json` once during 3D initialization.
- [ ] Feed current intersection phase and stage into `updateSignals` from the existing `trafficView` watcher.
- [ ] Destroy the renderer before disposing the MapV engine.
- [ ] Keep procedural buildings out of the implementation because the existing 3D Tiles already provide the authoritative buildings.

### Task 5: Verify and restart locally

**Files:**
- No production files beyond Tasks 1-4.

- [ ] Run focused tests, existing road/building/showcase tests, `vue-tsc --noEmit`, `npm run build`, and `git diff --check`.
- [ ] Restart the frontend with both showcase and roadside facility flags enabled.
- [ ] Restart or reuse the backend only for API/simulation state; static facilities must render even when the map GeoJSON request fails.
- [ ] Verify desktop and mobile screenshots, nonblank canvas pixels, camera presets, console errors, and signal fallback colors.
- [ ] Leave all changes local on `devFronted`; do not commit or push.
