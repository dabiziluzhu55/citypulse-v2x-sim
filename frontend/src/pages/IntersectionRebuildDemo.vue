<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  RealisticIntersectionRenderer,
  type DemoCameraPreset,
} from '../mapv/realistic/RealisticIntersectionRenderer'
import {
  loadIntersectionManifest,
  signalColorForState,
  type RealisticIntersectionManifest,
} from '../mapv/realistic/intersectionManifest'

const router = useRouter()
const sceneContainer = ref<HTMLDivElement | null>(null)
const manifest = ref<RealisticIntersectionManifest | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const phaseIndex = ref(0)
const remainingSeconds = ref(0)
const autoplay = ref(true)
const autoOrbit = ref(false)
const fps = ref(0)
const activeCamera = ref<DemoCameraPreset>('overview')
const playbackRate = 4

let scene: RealisticIntersectionRenderer | null = null
let phaseTimer: ReturnType<typeof setInterval> | null = null
let previousTick = 0

const activePhase = computed(() => manifest.value?.phases[phaseIndex.value] ?? null)
const linkRows = computed(() => {
  if (!manifest.value || !activePhase.value) return []
  return manifest.value.connections.map((connection) => ({
    ...connection,
    laneLabel: connection.fromLane === 0 ? '外侧车道' : '内侧车道',
    color: signalColorForState(activePhase.value?.state ?? '', connection.linkIndex),
  }))
})

function colorLabel(color: 'red' | 'amber' | 'green'): string {
  if (color === 'green') return '通行'
  if (color === 'amber') return '转换'
  return '停止'
}

function selectPhase(index: number): void {
  const phase = manifest.value?.phases[index]
  if (!phase) return
  phaseIndex.value = index
  remainingSeconds.value = phase.durationSeconds
  scene?.setSignalState(phase.state)
}

function selectCamera(preset: DemoCameraPreset): void {
  activeCamera.value = preset
  scene?.setCameraPreset(preset)
}

function syncAutoOrbit(): void {
  scene?.setAutoOrbit(autoOrbit.value)
}

function tick(now: number): void {
  if (!manifest.value || !autoplay.value) {
    previousTick = now
    return
  }
  if (previousTick === 0) previousTick = now
  remainingSeconds.value -= ((now - previousTick) / 1000) * playbackRate
  previousTick = now
  if (remainingSeconds.value > 0) return
  selectPhase((phaseIndex.value + 1) % manifest.value.phases.length)
}

async function initialize(): Promise<void> {
  if (!sceneContainer.value) return
  const loadedManifest = await loadIntersectionManifest()
  manifest.value = loadedManifest
  scene = new RealisticIntersectionRenderer(sceneContainer.value, loadedManifest, (nextFps) => {
    fps.value = nextFps
  })
  selectPhase(0)
  phaseTimer = setInterval(() => tick(performance.now()), 100)
  loading.value = false
}

onMounted(() => {
  document.title = 'demo_2 真实路口重建 · CityPulse'
  void initialize().catch((cause: unknown) => {
    error.value = cause instanceof Error ? cause.message : '真实路口场景初始化失败'
    loading.value = false
  })
})

onBeforeUnmount(() => {
  if (phaseTimer) clearInterval(phaseTimer)
  phaseTimer = null
  scene?.dispose()
  scene = null
})
</script>

<template>
  <main class="intersection-demo">
    <div ref="sceneContainer" class="intersection-demo__scene" />

    <header class="demo-header">
      <div class="demo-header__brand">
        <span class="demo-header__mark" aria-hidden="true" />
        <div>
          <p>CityPulse / Digital Twin</p>
          <h1>demo_2 真实路口重建</h1>
        </div>
      </div>
      <div class="demo-header__status">
        <span class="status-dot" />
        本地实时渲染
        <span class="status-divider" />
        {{ fps || '--' }} FPS
      </div>
      <button class="icon-command" type="button" title="返回系统总览" aria-label="返回系统总览" @click="router.push('/')">
        <span aria-hidden="true">←</span>
      </button>
    </header>

    <section class="scene-meta" aria-label="场景信息">
      <span>TLS {{ manifest?.tlsId ?? '317' }}</span>
      <span>SUMO 精确几何</span>
      <span>{{ manifest?.radiusMeters ?? 140 }} m 重建范围</span>
      <span>8 路受控转向</span>
    </section>

    <nav class="camera-toolbar" aria-label="相机机位">
      <button
        v-for="item in ([
          { id: 'overview', label: '全景' },
          { id: 'signals', label: '信号灯' },
          { id: 'markings', label: '俯视标线' },
        ] as const)"
        :key="item.id"
        type="button"
        :class="{ active: activeCamera === item.id }"
        @click="selectCamera(item.id)"
      >
        {{ item.label }}
      </button>
      <label class="switch-control">
        <input v-model="autoOrbit" type="checkbox" @change="syncAutoOrbit">
        <span class="switch-control__track"><span /></span>
        环绕
      </label>
    </nav>

    <aside class="signal-monitor">
      <div class="signal-monitor__heading">
        <div>
          <p>信号控制器</p>
          <h2>{{ activePhase?.label ?? '正在加载' }}</h2>
        </div>
        <strong>{{ Math.max(0, Math.ceil(remainingSeconds)) }}<small>s</small></strong>
      </div>

      <div class="phase-progress" aria-hidden="true">
        <span
          :style="{
            width: `${activePhase ? Math.max(0, remainingSeconds / activePhase.durationSeconds) * 100 : 0}%`,
          }"
        />
      </div>

      <div class="link-table">
        <div class="link-table__header">
          <span>LINK</span><span>入口车道</span><span>方向</span><span>状态</span>
        </div>
        <div v-for="row in linkRows" :key="row.linkIndex" class="link-table__row">
          <span class="link-id">{{ String(row.linkIndex).padStart(2, '0') }}</span>
          <span>{{ row.fromEdge }} · {{ row.fromLane }}</span>
          <span>{{ row.directionLabel }}</span>
          <span class="signal-state" :class="`signal-state--${row.color}`">
            <i />{{ colorLabel(row.color) }}
          </span>
        </div>
      </div>

      <div class="signal-monitor__footer">
        <span>相位状态</span>
        <code>{{ activePhase?.state ?? '--------' }}</code>
      </div>
    </aside>

    <section class="phase-console" aria-label="相位控制">
      <div class="phase-console__title">
        <span>相位回放</span>
        <small>{{ playbackRate }}x</small>
      </div>
      <div class="phase-buttons">
        <button
          v-for="phase in manifest?.phases ?? []"
          :key="phase.index"
          type="button"
          :class="{ active: phaseIndex === phase.index }"
          @click="selectPhase(phase.index)"
        >
          <span>P{{ phase.index + 1 }}</span>
          {{ phase.label.replace('向', '') }}
        </button>
      </div>
      <label class="switch-control switch-control--autoplay">
        <input v-model="autoplay" type="checkbox">
        <span class="switch-control__track"><span /></span>
        自动回放
      </label>
    </section>

    <div v-if="loading" class="scene-loading">
      <span />
      正在构建路口场景
    </div>
    <div v-if="error" class="scene-error">
      <strong>场景加载失败</strong>
      <span>{{ error }}</span>
    </div>
  </main>
</template>

<style scoped>
.intersection-demo {
  position: fixed;
  inset: 0;
  overflow: hidden;
  background: #101820;
  color: #f1f5f4;
  letter-spacing: 0;
}

.intersection-demo::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(8, 13, 17, 0.5), transparent 24%),
    linear-gradient(0deg, rgba(8, 13, 17, 0.48), transparent 28%);
  pointer-events: none;
}

.intersection-demo__scene,
.intersection-demo__scene :deep(canvas) {
  width: 100%;
  height: 100%;
  display: block;
}

.demo-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 3;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  min-height: 76px;
  padding: 12px 22px;
  border-bottom: 1px solid rgba(212, 224, 220, 0.14);
  background: rgba(10, 17, 22, 0.82);
  backdrop-filter: blur(16px);
}

.demo-header__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.demo-header__mark {
  width: 5px;
  height: 39px;
  background: #d7a840;
  box-shadow: 0 0 18px rgba(215, 168, 64, 0.34);
}

.demo-header p,
.signal-monitor p {
  margin: 0 0 3px;
  color: #879a9b;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.demo-header h1 {
  margin: 0;
  overflow: hidden;
  font-size: 18px;
  font-weight: 650;
  letter-spacing: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.demo-header__status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #b8c5c2;
  font-size: 12px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4ee08b;
  box-shadow: 0 0 11px rgba(78, 224, 139, 0.8);
}

.status-divider {
  width: 1px;
  height: 15px;
  margin: 0 3px;
  background: rgba(255, 255, 255, 0.18);
}

.icon-command {
  justify-self: end;
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: 1px solid rgba(220, 230, 227, 0.2);
  border-radius: 6px;
  background: rgba(44, 56, 59, 0.74);
  color: #f4f7f6;
  font-size: 23px;
  cursor: pointer;
}

.scene-meta {
  position: absolute;
  top: 92px;
  left: 22px;
  z-index: 3;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-width: calc(100vw - 450px);
}

.scene-meta span {
  padding: 6px 9px;
  border: 1px solid rgba(222, 230, 227, 0.14);
  border-radius: 4px;
  background: rgba(16, 26, 31, 0.68);
  color: #aab8b6;
  font-size: 11px;
  backdrop-filter: blur(8px);
}

.camera-toolbar {
  position: absolute;
  top: 92px;
  right: 22px;
  z-index: 4;
  display: flex;
  align-items: center;
  padding: 4px;
  border: 1px solid rgba(220, 230, 227, 0.16);
  border-radius: 7px;
  background: rgba(14, 23, 28, 0.86);
  backdrop-filter: blur(12px);
}

.camera-toolbar button,
.phase-buttons button {
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #a7b4b2;
  font: inherit;
  cursor: pointer;
}

.camera-toolbar button {
  min-width: 58px;
  height: 31px;
  padding: 0 10px;
  font-size: 12px;
}

.camera-toolbar button:hover,
.camera-toolbar button.active {
  background: #394b4c;
  color: #fff;
}

.switch-control {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 8px;
  color: #a7b4b2;
  font-size: 12px;
  cursor: pointer;
}

.switch-control input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.switch-control__track {
  position: relative;
  width: 30px;
  height: 16px;
  border-radius: 8px;
  background: #394347;
  transition: background 0.2s;
}

.switch-control__track span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #b5c0bd;
  transition: transform 0.2s, background 0.2s;
}

.switch-control input:checked + .switch-control__track {
  background: #3f7d61;
}

.switch-control input:checked + .switch-control__track span {
  background: #e8fff2;
  transform: translateX(14px);
}

.signal-monitor {
  position: absolute;
  top: 140px;
  right: 22px;
  z-index: 3;
  width: 370px;
  border: 1px solid rgba(220, 230, 227, 0.16);
  border-radius: 7px;
  background: rgba(13, 22, 27, 0.88);
  box-shadow: 0 18px 42px rgba(0, 0, 0, 0.26);
  backdrop-filter: blur(15px);
}

.signal-monitor__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 17px 12px;
}

.signal-monitor h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 620;
  letter-spacing: 0;
}

.signal-monitor__heading strong {
  color: #f1c55c;
  font-size: 26px;
  font-variant-numeric: tabular-nums;
}

.signal-monitor__heading small {
  margin-left: 2px;
  color: #8b9997;
  font-size: 11px;
  font-weight: 500;
}

.phase-progress {
  height: 2px;
  background: rgba(255, 255, 255, 0.08);
}

.phase-progress span {
  display: block;
  height: 100%;
  background: #d7a840;
  transition: width 0.1s linear;
}

.link-table {
  padding: 8px 12px;
}

.link-table__header,
.link-table__row {
  display: grid;
  grid-template-columns: 42px 1.4fr 0.7fr 0.8fr;
  align-items: center;
  min-height: 31px;
  gap: 7px;
}

.link-table__header {
  color: #718180;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.link-table__row {
  border-top: 1px solid rgba(255, 255, 255, 0.055);
  color: #aab7b5;
  font-size: 11px;
}

.link-id {
  color: #e0e6e4;
  font-family: 'Cascadia Mono', Consolas, monospace;
}

.signal-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.signal-state i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 8px currentColor;
}

.signal-state--red { color: #ff635c; }
.signal-state--amber { color: #ffc957; }
.signal-state--green { color: #4ee08b; }

.signal-monitor__footer {
  display: flex;
  justify-content: space-between;
  padding: 10px 17px 13px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: #718180;
  font-size: 11px;
}

.signal-monitor__footer code {
  color: #d9e2df;
  font-size: 12px;
  letter-spacing: 0.14em;
}

.phase-console {
  position: absolute;
  left: 22px;
  bottom: 22px;
  z-index: 3;
  display: grid;
  grid-template-columns: auto auto auto;
  align-items: center;
  gap: 14px;
  padding: 9px 11px;
  border: 1px solid rgba(220, 230, 227, 0.16);
  border-radius: 7px;
  background: rgba(13, 22, 27, 0.88);
  backdrop-filter: blur(14px);
}

.phase-console__title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
  color: #dce4e2;
  font-size: 12px;
}

.phase-console__title small {
  padding: 2px 4px;
  border-radius: 3px;
  background: #594d2a;
  color: #f2d27c;
  font-size: 9px;
}

.phase-buttons {
  display: flex;
  gap: 3px;
}

.phase-buttons button {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 34px;
  padding: 0 9px;
  font-size: 11px;
}

.phase-buttons button span {
  color: #6f807e;
  font-family: 'Cascadia Mono', Consolas, monospace;
}

.phase-buttons button:hover,
.phase-buttons button.active {
  background: #354749;
  color: #fff;
}

.phase-buttons button.active span {
  color: #f1c55c;
}

.switch-control--autoplay {
  min-height: 34px;
  border-left: 1px solid rgba(255, 255, 255, 0.09);
  padding-left: 14px;
}

.scene-loading,
.scene-error {
  position: absolute;
  inset: 0;
  z-index: 8;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #101820;
  color: #c4cfcc;
  font-size: 13px;
}

.scene-loading span {
  width: 19px;
  height: 19px;
  border: 2px solid rgba(255, 255, 255, 0.15);
  border-top-color: #d7a840;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.scene-error {
  flex-direction: column;
  color: #e6aaa5;
}

@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 900px) {
  .demo-header {
    grid-template-columns: 1fr auto;
    min-height: 66px;
    padding: 10px 14px;
  }

  .demo-header__status { display: none; }
  .demo-header h1 { font-size: 15px; }
  .scene-meta { top: 77px; left: 12px; max-width: calc(100vw - 24px); }
  .scene-meta span:nth-child(n + 3) { display: none; }

  .camera-toolbar {
    top: 116px;
    left: 12px;
    right: auto;
  }

  .camera-toolbar button { min-width: 52px; padding: 0 7px; }

  .signal-monitor {
    top: auto;
    right: 12px;
    bottom: 79px;
    width: min(330px, calc(100vw - 24px));
    max-height: 32vh;
    overflow: auto;
  }

  .link-table__row { min-height: 28px; }

  .phase-console {
    left: 12px;
    right: 12px;
    bottom: 12px;
    grid-template-columns: auto 1fr auto;
    gap: 6px;
    overflow: hidden;
  }

  .phase-console__title span,
  .phase-buttons button:not(.active) {
    display: none;
  }

  .phase-buttons button.active { display: flex; }
}

@media (max-width: 540px) {
  .switch-control--autoplay { border-left: 0; padding-left: 6px; }
  .signal-monitor__heading { padding: 11px 13px 8px; }
  .link-table { padding: 5px 9px; }
  .signal-monitor__footer { padding: 8px 13px; }
}
</style>
