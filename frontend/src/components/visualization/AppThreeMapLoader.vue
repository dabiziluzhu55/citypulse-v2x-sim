<script lang="ts">
let nextDevModuleRequest = 0

function loadBaiduThreeMap(): Promise<typeof import('./BaiduThreeMap.vue')> {
  if (import.meta.env.DEV) {
    const sourceUrl = `/src/components/visualization/BaiduThreeMap.vue?map3dRetry=${nextDevModuleRequest++}`
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
  onErrorCaptured,
  ref,
} from 'vue'
import {
  classifyMap3dFailure,
  type Map3dFailure,
} from '../../mapv/map3dLoadRecovery'

const MAX_AUTO_RETRIES = 2
const RETRY_DELAYS_MS = [500, 1500] as const

const emit = defineEmits<{
  fatal: [failure: Map3dFailure]
}>()

const loading = ref(true)
let fatalReported = false

const EmptyFailurePlaceholder = defineComponent({
  name: 'Map3dFailurePlaceholder',
  setup: () => () => h('span', { 'aria-hidden': 'true' }),
})

function reportFatal(cause: unknown): void {
  if (fatalReported) return
  fatalReported = true
  loading.value = false
  emit('fatal', classifyMap3dFailure(cause))
}

const AsyncBaiduThreeMap = defineAsyncComponent({
  loader: async () => {
    loading.value = true
    const component = await loadBaiduThreeMap()
    loading.value = false
    return component
  },
  delay: 120,
  timeout: 20_000,
  suspensible: false,
  errorComponent: EmptyFailurePlaceholder,
  onError(error, retry, fail, attempts) {
    if (attempts <= MAX_AUTO_RETRIES) {
      const delay = RETRY_DELAYS_MS[attempts - 1] ?? RETRY_DELAYS_MS.at(-1) ?? 0
      window.setTimeout(retry, delay)
      return
    }
    reportFatal(error)
    fail()
  },
})

onErrorCaptured((cause) => {
  reportFatal(cause)
  return false
})
</script>

<template>
  <AsyncBaiduThreeMap @fatal="reportFatal" />
  <div v-if="loading" class="app-three-map-loader" role="status" aria-live="polite">
    <span class="app-three-map-loader__spinner" aria-hidden="true" />
    <span>正在加载百度三维地图与高精度路口</span>
  </div>
</template>

<style scoped>
.app-three-map-loader {
  position: fixed;
  inset: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #07111d;
  color: #9edfff;
  font-size: 14px;
  pointer-events: none;
}

.app-three-map-loader__spinner {
  width: 22px;
  height: 22px;
  border: 2px solid rgba(158, 223, 255, 0.28);
  border-top-color: #21e6ff;
  border-radius: 50%;
  animation: app-three-map-spin 0.8s linear infinite;
}

@keyframes app-three-map-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .app-three-map-loader__spinner {
    animation: none;
  }
}
</style>
