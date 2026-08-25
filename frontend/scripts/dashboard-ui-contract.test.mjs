import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const sidebarSource = readFileSync(
  new URL('../src/components/dashboard/LeftSidebarPanel.vue', import.meta.url),
  'utf8',
)
const timeStepperSource = readFileSync(
  new URL('../src/components/dashboard/HourMinuteStepper.vue', import.meta.url),
  'utf8',
)
const dashboardCss = readFileSync(
  new URL('../src/assets/styles/dashboard.css', import.meta.url),
  'utf8',
)
const bottomIconsSource = readFileSync(
  new URL('../src/components/dashboard/chrome/DashboardBottomIcons.vue', import.meta.url),
  'utf8',
)
const overlaySource = readFileSync(
  new URL('../src/composables/useDashboardOverlay.ts', import.meta.url),
  'utf8',
)
const homeSource = readFileSync(
  new URL('../src/pages/HomePage.vue', import.meta.url),
  'utf8',
)
const backgroundMapSource = readFileSync(
  new URL('../src/components/visualization/AppBackgroundMap.vue', import.meta.url),
  'utf8',
)
const basemapSource = readFileSync(
  new URL('../src/constants/mapBasemaps.ts', import.meta.url),
  'utf8',
)
const simulationMapSource = readFileSync(
  new URL('../src/composables/useSimulationMap.ts', import.meta.url),
  'utf8',
)
const baiduThreeMapSource = readFileSync(
  new URL('../src/components/visualization/BaiduThreeMap.vue', import.meta.url),
  'utf8',
)

test('uses four immediate dark hour-minute steppers and one five-mode algorithm dropdown', () => {
  assert.match(sidebarSource, />仿真展示时间</)
  assert.equal(sidebarSource.match(/<HourMinuteStepper/g)?.length, 4)
  assert.doesNotMatch(sidebarSource, /<el-time-picker|<el-time-select/)
  assert.match(timeStepperSource, /stepClockHour/)
  assert.match(timeStepperSource, /stepClockMinute/)
  assert.match(timeStepperSource, /background: #071f38/)
  assert.match(timeStepperSource, /color: #fff/)
  assert.match(timeStepperSource, /emit\('update:modelValue', next\)/)
  assert.match(sidebarSource, /class="left-sidebar__algorithm-select"/)
  assert.doesNotMatch(sidebarSource, /type="radio" name="sidebar-algorithm"/)
  assert.doesNotMatch(sidebarSource, /后端暂未提供MAPPO算法|IPPO 仅支持雄安20路口场景/)
})

test('toggles both side panels without unmounting them and labels the roadside device', () => {
  assert.match(overlaySource, /const sidePanelsCollapsed = ref\(false\)/)
  assert.match(overlaySource, /function toggleSidePanels\(\)/)
  assert.match(homeSource, /'is-side-panels-collapsed': sidePanelsCollapsed/)
  assert.match(bottomIconsSource, /toggleSidePanels/)
  assert.match(bottomIconsSource, /aria-pressed="sidePanelsCollapsed"/)
  assert.match(bottomIconsSource, /收起两侧面板/)
  assert.match(bottomIconsSource, /展开两侧面板/)
  assert.match(bottomIconsSource, />路侧设备</)
})

test('matches the communication-dialog chrome and removes the English event eyebrow', () => {
  assert.match(sidebarSource, /width: min\(1000px, calc\(100vw - 48px\)\)/)
  assert.match(sidebarSource, /clip-path: polygon\(18px 0, 35% 0/)
  assert.doesNotMatch(sidebarSource, /SCENARIO EVENT/)
  assert.match(sidebarSource, /:min="MIN_MAJOR_EVENT_VEHICLE_COUNT"/)
  assert.match(sidebarSource, /:max="MAX_MAJOR_EVENT_VEHICLE_COUNT"/)
  assert.match(sidebarSource, /background: #092846/)
})

test('docks every bottom navigation layer on the viewport edge', () => {
  for (const variable of [
    '--dashboard-bottom-dock-offset-y',
    '--dashboard-bottom-center-offset-y',
    '--dashboard-bottom-icons-offset-y',
  ]) {
    assert.match(dashboardCss, new RegExp(`${variable}: 0px`))
  }
  assert.match(bottomIconsSource, /bottom: var\(--dashboard-bottom-icons-offset-y, 0px\)/)
})

test('keeps the viewing-intersection selector available while a session is active', () => {
  const selector = homeSource.match(/<select[\s\S]*?<\/select>/)?.[0] ?? ''
  assert.ok(selector)
  assert.doesNotMatch(selector, /:disabled=|\sdisabled(?:\s|>)/)
  assert.match(selector, /title="选择查看路口"/)
})

test('uses the labeled dark 2D map and follows the active intersection safely', () => {
  assert.match(basemapSource, /DEFAULT_APP_BASEMAP:\s*BasemapVariant\s*=\s*'carto_dark'/)
  assert.match(backgroundMapSource, /useSimulationMap\(activeIntersectionId\)/)
  assert.match(backgroundMapSource, /duration:\s*700/)
  assert.match(backgroundMapSource, /当前路口路网加载失败，已保留深色底图定位/)
  assert.match(simulationMapSource, /const revision = \+\+loadRevision/)
  assert.match(simulationMapSource, /revision !== loadRevision \|\| id !== resolveId\(\)/)
})

test('keeps render and simulation throughput diagnostics internal to development mode', () => {
  assert.match(baiduThreeMapSource, /showRenderDiagnostics = import\.meta\.env\.DEV/)
  assert.match(baiduThreeMapSource, /__CITYPULSE_VEHICLE_DIAGNOSTICS__/)
  assert.match(baiduThreeMapSource, /simulationProgressRate: stats\.sourceRate/)
  assert.doesNotMatch(baiduThreeMapSource, />\s*实际倍率\s*</)
})

test('keeps lane-closure disabled labels compact and confirmation above the editor', () => {
  assert.match(sidebarSource, /accessibleLabel: laneClosureUnavailable/)
  assert.match(sidebarSource, /:aria-label="option\.accessibleLabel"/)
  assert.doesNotMatch(sidebarSource, /unavailableLabel/)
  assert.match(homeSource, /<Teleport to="body">[\s\S]*class="config-change-dialog"/)
  assert.match(homeSource, /z-index: 3100/)
  assert.match(sidebarSource, /z-index: 3000/)
  assert.match(sidebarSource, /z-index: 3200/)
  assert.match(homeSource, /configChangeDialogRef\.value\?\.focus\(\)/)
})

test('keeps one full-width current-simulation export action', () => {
  assert.doesNotMatch(sidebarSource, /保存仿真场景|saveConfig\s*\(/)
  assert.equal(sidebarSource.match(/导出当前仿真场景/g)?.length, 1)
  assert.match(sidebarSource, /gridTemplateColumns: 'minmax\(0, 1fr\)'/)
  assert.doesNotMatch(sidebarSource, /fileActions\.buttonWidth/)
})
