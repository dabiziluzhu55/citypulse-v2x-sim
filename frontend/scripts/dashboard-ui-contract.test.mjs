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
const roadsideSource = readFileSync(
  new URL('../src/components/dashboard/RoadsideDevicesPanel.vue', import.meta.url),
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

test('toggles both side panels from the top toolbar and opens the roadside device panel', () => {
  assert.match(overlaySource, /const sidePanelsCollapsed = ref\(false\)/)
  assert.match(overlaySource, /function toggleSidePanels\(\)/)
  assert.match(homeSource, /'is-side-panels-collapsed': sidePanelsCollapsed/)
  assert.match(homeSource, /@click="toggleSidePanels"/)
  assert.match(overlaySource, /const roadsideDevicePanelOpen = ref\(false\)/)
  assert.match(overlaySource, /function toggleRoadsideDevicePanel\(\)/)
  assert.match(bottomIconsSource, /toggleRoadsideDevicePanel/)
  assert.match(bottomIconsSource, /aria-pressed="roadsideDevicePanelOpen"/)
  assert.match(homeSource, /<RoadsideDevicesPanel/)
  assert.match(homeSource, /communication-overlay--roadside/)
  assert.match(bottomIconsSource, />路侧设备画面</)
  assert.match(roadsideSource, /\/roadside-media\/demo_14\.mp4/)
  assert.match(roadsideSource, /\/roadside-media\/demo_15\.mp4/)
  assert.match(roadsideSource, /\/roadside-media\/demo_19\.mp4/)
  assert.match(roadsideSource, /preload="metadata"/)
  assert.match(roadsideSource, /autoplay/)
  assert.match(roadsideSource, /\bmuted\b/)
  assert.match(roadsideSource, /\bloop\b/)
  assert.match(roadsideSource, /playsinline/)
  assert.match(roadsideSource, /video\.pause\(\)/)
  assert.doesNotMatch(roadsideSource, /LIVE|实时监控/)
  assert.doesNotMatch(roadsideSource, /https?:\/\/192\.|\/home\/kemove/)
})

test('matches the communication-dialog chrome and removes the English event eyebrow', () => {
  assert.match(sidebarSource, /width: min\(1000px, calc\(100vw - 48px\)\)/)
  assert.match(sidebarSource, /clip-path: polygon\(18px 0, 35% 0/)
  assert.doesNotMatch(sidebarSource, /SCENARIO EVENT/)
  assert.match(sidebarSource, /:min="MIN_MAJOR_EVENT_VEHICLE_COUNT"/)
  assert.match(sidebarSource, /:max="MAX_MAJOR_EVENT_VEHICLE_COUNT"/)
  assert.match(sidebarSource, /background: #092846/)
})

test('keeps every bottom navigation layer aligned with the decorative rails', () => {
  assert.match(dashboardCss, /--dashboard-bottom-dock-offset-y: 12px/)
  assert.match(dashboardCss, /--dashboard-bottom-center-offset-y: 12px/)
  assert.match(dashboardCss, /--dashboard-bottom-icons-offset-y: 20px/)
  assert.match(bottomIconsSource, /bottom: var\(--dashboard-bottom-icons-offset-y, 20px\)/)
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

test('decouples Copilot from the AI takeover switch and requires an event selection', () => {
  const aiPanelSource = readFileSync(
    new URL('../src/components/dashboard/AiControlPanel.vue', import.meta.url),
    'utf8',
  )
  assert.match(sidebarSource, /请先配置至少一个扰动事件，再开启AI管控/)
  assert.match(sidebarSource, /选择主要扰动事件及主要路口/)
  assert.match(sidebarSource, /AI管控仅针对一个扰动事件进行管控，请选择管控事件和路口/)
  assert.match(sidebarSource, /ai-takeover-dialog__panel/)
  assert.match(sidebarSource, /主要扰动事件/)
  assert.match(sidebarSource, /主要路口/)
  assert.doesNotMatch(sidebarSource, /开启AI事件接管/)
  assert.doesNotMatch(sidebarSource, /其他扰动仍会正常作用于仿真/)
  assert.doesNotMatch(sidebarSource, /class="runtime-error-modal ai-takeover-dialog"/)
  assert.match(sidebarSource, /:model-value="aiControlEnabled"/)
  assert.doesNotMatch(sidebarSource, /v-model="aiControlEnabled"/)
  assert.match(sidebarSource, /selectedAiEventId/)
  assert.match(sidebarSource, /selectedAiIntersectionId/)
  assert.match(sidebarSource, /已选AI管控目标已不存在，请重新选择后再开启AI管控/)
  assert.match(sidebarSource, /clearAiTakeoverSelection/)
  assert.match(aiPanelSource, /question\.value\.trim\(\) && !submitting\.value/)
  assert.match(aiPanelSource, /交通知识问答 · 态势分析 · AI事件管控/)
  assert.match(aiPanelSource, /CITYPULSE_QWEN_BENCHMARK_METRICS/)
  assert.match(aiPanelSource, /ai-control-panel__effect-grid/)
  assert.match(aiPanelSource, /模型验证 · 相对固定配时/)
  assert.doesNotMatch(aiPanelSource, /aiControlEnabled/)
  assert.doesNotMatch(aiPanelSource, /snapshot\?\.ai_takeover\?\.ai_enabled/)
  assert.match(aiPanelSource, /当前未开启交通仿真和AI管控/)
  assert.doesNotMatch(aiPanelSource, /请先启动仿真，再向交通 Copilot 提问/)
  assert.doesNotMatch(overlaySource, /ai_enabled/)
  assert.doesNotMatch(overlaySource, /aiControlEnabled/)
  assert.match(bottomIconsSource, /toggleAiControlPanel/)
  assert.doesNotMatch(bottomIconsSource, /ai_enabled|aiControlEnabled/)
  assert.match(homeSource, /:session-id="sessionId"/)
})
