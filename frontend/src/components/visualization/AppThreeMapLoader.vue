<script lang="ts">
function loadBaiduThreeMap(
  recoverModuleCache = false,
): Promise<typeof import('./BaiduThreeMap.vue')> {
  if (import.meta.env.DEV && recoverModuleCache) {
    const sourceUrl = `/src/components/visualization/BaiduThreeMap.vue?map3dCacheRecovery=${Date.now()}`
    return import(/* @vite-ignore */ sourceUrl) as Promise<typeof import('./BaiduThreeMap.vue')>
  }
  return import('./BaiduThreeMap.vue')
}
</script>

<script setup lang="ts">
import {
  defineAsyncComponent,
  defineComponent,
  h,
  nextTick,
  onErrorCaptured,
  onUnmounted,
  ref,
  shallowRef,
  watch,
} from 'vue'
import {
  classifyMap3dFailure,
  type Map3dFailure,
} from '../../mapv/map3dLoadRecovery'
import {
  MAP3D_MODULE_LOAD_TIMEOUT_MS,
  MAP3D_PRESENTATION_HARD_TIMEOUT_MS,
} from '../../mapv/map3dPresentationReadiness'

const MAX_AUTO_RETRIES = 2
const RETRY_DELAYS_MS = [500, 1500] as const

const emit = defineEmits<{
  return2d: [failure: Map3dFailure | null]
  stateChange: [state: 'loading' | 'ready' | 'error']
}>()

const state = ref<'loading' | 'ready' | 'error'>('loading')
const loadingMessage = ref('正在加载三维场景')
const failure = ref<Map3dFailure | null>(null)
const componentKey = ref(0)
const componentVisible = ref(true)
let timeoutId: ReturnType<typeof setTimeout> | null = null

const EmptyFailurePlaceholder = defineComponent({
  name: 'Map3dFailurePlaceholder',
  setup: () => () => h('span', { 'aria-hidden': 'true' }),
})

function clearLoadTimeout(): void {
  if (!timeoutId) return
  clearTimeout(timeoutId)
  timeoutId = null
}

function reportFailure(cause: unknown): void {
  if (state.value === 'error') return
  clearLoadTimeout()
  failure.value = classifyMap3dFailure(cause)
  state.value = 'error'
}

function armSceneTimeout(): void {
  clearLoadTimeout()
  timeoutId = setTimeout(() => {
    reportFailure(new Error(
      `3D 场景加载超过 ${MAP3D_PRESENTATION_HARD_TIMEOUT_MS / 1000} 秒：${loadingMessage.value}`,
    ))
  }, MAP3D_PRESENTATION_HARD_TIMEOUT_MS)
}

function createAsyncBaiduThreeMap(recoverModuleCache = false) {
  return defineAsyncComponent({
    loader: async () => {
      const module = await loadBaiduThreeMap(recoverModuleCache)
      armSceneTimeout()
      return module
    },
    delay: 0,
    timeout: MAP3D_MODULE_LOAD_TIMEOUT_MS,
    suspensible: false,
    errorComponent: EmptyFailurePlaceholder,
    onError(error, retry, fail, attempts) {
      if (attempts <= MAX_AUTO_RETRIES) {
        const delay = RETRY_DELAYS_MS[attempts - 1] ?? RETRY_DELAYS_MS.at(-1) ?? 0
        window.setTimeout(retry, delay)
        return
      }
      reportFailure(error)
      fail()
    },
  })
}

const asyncBaiduThreeMap = shallowRef(createAsyncBaiduThreeMap())

function handleLoading(message: string): void {
  if (state.value === 'error') return
  loadingMessage.value = message || '正在加载三维场景'
  if (state.value === 'ready' && timeoutId === null) armSceneTimeout()
  state.value = 'loading'
}

function handleReady(): void {
  if (state.value === 'error') return
  clearLoadTimeout()
  state.value = 'ready'
}

async function retryThreeMap(): Promise<void> {
  const recoverModuleCache = failure.value?.code === 'module-cache'
  clearLoadTimeout()
  failure.value = null
  loadingMessage.value = '正在重新加载三维场景'
  state.value = 'loading'
  componentVisible.value = false
  await nextTick()
  componentKey.value += 1
  asyncBaiduThreeMap.value = createAsyncBaiduThreeMap(recoverModuleCache)
  componentVisible.value = true
}

function returnTo2d(): void {
  clearLoadTimeout()
  emit('return2d', failure.value)
}

onErrorCaptured((cause) => {
  reportFailure(cause)
  return false
})

watch(state, (nextState) => emit('stateChange', nextState), { immediate: true })
onUnmounted(clearLoadTimeout)
</script>

<template>
  <component
    v-if="componentVisible"
    :is="asyncBaiduThreeMap"
    :key="componentKey"
    @fatal="reportFailure"
    @loading="handleLoading"
    @ready="handleReady"
  />

  <Transition name="map3d-loading-fade">
    <div
      v-if="state !== 'ready'"
      class="app-three-map-loader"
      :class="{ 'is-error': state === 'error' }"
      role="status"
      aria-live="polite"
    >
      <div class="app-three-map-loader__content">
        <span
          v-if="state === 'loading'"
          class="app-three-map-loader__spinner"
          aria-hidden="true"
        />
        <span v-else class="app-three-map-loader__error-mark" aria-hidden="true">!</span>
        <strong>{{ state === 'error' ? '三维场景加载失败' : loadingMessage }}</strong>
        <span v-if="state === 'loading'" class="app-three-map-loader__secondary">
          正在准备地图、建筑、道路与路口设施
        </span>
        <span v-else class="app-three-map-loader__secondary">
          {{ failure?.message }}
        </span>
        <span v-if="state === 'error' && failure?.detail" class="app-three-map-loader__detail">
          {{ failure.detail }}
        </span>
        <div v-if="state === 'error'" class="app-three-map-loader__actions">
          <button type="button" @click="retryThreeMap">重新加载 3D</button>
          <button type="button" class="is-secondary" @click="returnTo2d">返回 2D</button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.app-three-map-loader {
  position: fixed;
  inset: 0;
  z-index: 1;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #000;
  color: #dff8ff;
  pointer-events: auto;
}

.app-three-map-loader__content {
  display: grid;
  justify-items: center;
  gap: 10px;
  width: min(420px, calc(100vw - 48px));
  text-align: center;
}

.app-three-map-loader__content strong {
  font-size: 15px;
  font-weight: 600;
}

.app-three-map-loader__secondary,
.app-three-map-loader__detail {
  color: #8eb7c7;
  font-size: 12px;
  line-height: 1.5;
}

.app-three-map-loader__detail {
  max-width: 100%;
  overflow-wrap: anywhere;
  color: #c4d7de;
}

.app-three-map-loader__spinner,
.app-three-map-loader__error-mark {
  width: 24px;
  height: 24px;
}

.app-three-map-loader__spinner {
  border: 2px solid rgba(158, 223, 255, 0.28);
  border-top-color: #21e6ff;
  border-radius: 50%;
  animation: app-three-map-spin 0.8s linear infinite;
}

.app-three-map-loader__error-mark {
  display: grid;
  place-items: center;
  border: 1px solid #ff7e7e;
  border-radius: 50%;
  color: #ffb0b0;
  font-size: 16px;
  font-weight: 700;
}

.app-three-map-loader__actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

.app-three-map-loader__actions button {
  height: 34px;
  padding: 0 14px;
  border: 1px solid #21e6ff;
  border-radius: 4px;
  background: #083a55;
  color: #e7fbff;
  cursor: pointer;
}

.app-three-map-loader__actions button.is-secondary {
  border-color: #607580;
  background: #17242a;
}

.map3d-loading-fade-leave-active {
  transition: opacity 180ms ease;
}

.map3d-loading-fade-leave-to {
  opacity: 0;
}

@keyframes app-three-map-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .app-three-map-loader__spinner {
    animation: none;
  }

  .map3d-loading-fade-leave-active {
    transition: none;
  }
}
</style>
