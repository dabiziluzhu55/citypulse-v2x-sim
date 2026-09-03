<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { SimulationIntersectionRuntime } from '../../types/simulation'
import {
  parseSceneFacilityManifest,
  type SceneFacilityManifest,
} from '../../mapv/showcaseLayers/sceneFacilities'
import {
  loadIntersectionEnvironmentManifest,
} from '../../mapv/realistic/intersectionEnvironmentManifest'
import { fetchJsonAsset } from '../../utils/fetchJsonAsset'

const props = defineProps<{
  intersectionId: string
  runtime: SimulationIntersectionRuntime | null
}>()

const emit = defineEmits<{ close: [] }>()
const manifest = ref<SceneFacilityManifest | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let loadGeneration = 0

watch(
  () => props.intersectionId,
  async (intersectionId) => {
    const generation = ++loadGeneration
    manifest.value = null
    error.value = null
    loading.value = true
    try {
      const environment = await loadIntersectionEnvironmentManifest(intersectionId)
      if (!environment.facilitiesUrl) {
        throw new Error('当前路口没有配置路侧设施清单')
      }
      const raw = await fetchJsonAsset<unknown>(
        environment.facilitiesUrl,
        `路口 ${intersectionId} 路侧设施`,
      )
      const parsed = parseSceneFacilityManifest(raw)
      if (parsed.intersectionId !== intersectionId) {
        throw new Error(`设施清单 ${parsed.intersectionId} 与当前路口 ${intersectionId} 不一致`)
      }
      if (generation === loadGeneration) manifest.value = parsed
    } catch (reason) {
      if (generation === loadGeneration) {
        error.value = reason instanceof Error ? reason.message : '加载路侧设施失败'
      }
    } finally {
      if (generation === loadGeneration) loading.value = false
    }
  },
  { immediate: true },
)

const rows = computed(() => {
  const facilities = manifest.value
  if (!facilities) return []
  return [
    {
      type: '信号控制设备',
      count: facilities.signals.length,
      state: props.runtime
        ? `${props.runtime.stage} / 相位 ${props.runtime.current_phase}`
        : '已配置，等待仿真快照',
      linked: !!props.runtime,
    },
    {
      type: '路侧摄像机',
      count: facilities.cameras.length,
      state: '三维资源已配置，暂无实时摄像机遥测',
      linked: false,
    },
    {
      type: '道路照明设备',
      count: facilities.lamps.length,
      state: '三维资源已配置，暂无实时灯具遥测',
      linked: false,
    },
  ]
})
</script>

<template>
  <section class="roadside-device-panel" aria-label="路侧设备">
    <header class="roadside-device-panel__header">
      <div>
        <strong>路侧设备</strong>
        <span>{{ intersectionId }}</span>
      </div>
      <button type="button" title="关闭" aria-label="关闭路侧设备" @click="emit('close')">×</button>
    </header>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-skeleton v-else-if="loading" animated :rows="5" />
    <el-table v-else :data="rows" stripe empty-text="当前路口没有路侧设备">
      <el-table-column prop="type" label="设备类型" min-width="170" />
      <el-table-column prop="count" label="数量" width="100" align="center" />
      <el-table-column label="接入状态" min-width="330">
        <template #default="{ row }">
          <span :class="['roadside-device-panel__state', { 'is-linked': row.linked }]">
            {{ row.state }}
          </span>
        </template>
      </el-table-column>
    </el-table>

    <p class="roadside-device-panel__notice">
      信号设备与实时仿真相位联动；摄像机和路灯当前仅有三维资源，未伪造在线遥测。
    </p>
  </section>
</template>

<style scoped>
.roadside-device-panel {
  position: relative;
  width: min(880px, calc(100vw - 48px));
  min-height: 430px;
  padding: 28px 36px;
  border: 1px solid rgba(91, 159, 255, .72);
  clip-path: polygon(18px 0, calc(100% - 18px) 0, 100% 18px, 100% calc(100% - 18px), calc(100% - 18px) 100%, 18px 100%, 0 calc(100% - 18px), 0 18px);
  background: linear-gradient(180deg, rgba(20, 48, 89, .97), rgba(8, 35, 72, .97));
  box-shadow: inset 0 0 42px rgba(69, 136, 225, .18), 0 0 26px rgba(18, 110, 218, .24);
  color: #f4fbff;
  pointer-events: auto;
}
.roadside-device-panel__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.roadside-device-panel__header > div { display: flex; align-items: baseline; gap: 14px; }
.roadside-device-panel__header strong { font-size: 24px; letter-spacing: 2px; }
.roadside-device-panel__header span { color: #65e8ff; font-size: 14px; }
.roadside-device-panel__header button { width: 30px; height: 30px; padding: 0; border: 1px solid rgba(98, 216, 255, .45); border-radius: 50%; background: rgba(2, 21, 44, .72); color: #ccefff; font-size: 21px; cursor: pointer; }
.roadside-device-panel :deep(.el-table) { --el-table-border-color: rgba(117, 191, 255, .12); --el-table-bg-color: transparent; --el-table-tr-bg-color: transparent; --el-fill-color-lighter: rgba(98, 148, 211, .12); background: transparent; color: #eefaff; }
.roadside-device-panel :deep(th.el-table__cell) { background: rgba(48, 91, 151, .92); color: #fff; }
.roadside-device-panel :deep(td.el-table__cell) { background: transparent; }
.roadside-device-panel__state { color: #9bb5c8; }
.roadside-device-panel__state.is-linked { color: #55f3bd; text-shadow: 0 0 8px rgba(85, 243, 189, .35); }
.roadside-device-panel__notice { margin: 28px 0 0; color: #8fb1c8; font-size: 13px; line-height: 1.7; }
</style>
