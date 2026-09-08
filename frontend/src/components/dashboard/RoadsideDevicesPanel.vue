<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const emit = defineEmits<{ close: [] }>()

const CAMERAS = [
  {
    id: 'demo_14',
    title: '路口14 · 路侧高杆摄像机',
    src: '/roadside-media/demo_14.mp4',
  },
  {
    id: 'demo_15',
    title: '路口15 · 路侧高杆摄像机',
    src: '/roadside-media/demo_15.mp4',
  },
  {
    id: 'demo_19',
    title: '路口19 · 路侧高杆摄像机',
    src: '/roadside-media/demo_19.mp4',
  },
] as const

const panelRef = ref<HTMLElement | null>(null)
const failedSources = ref<Record<string, boolean>>({})

function collectVideos(): HTMLVideoElement[] {
  if (!panelRef.value) return []
  return [...panelRef.value.querySelectorAll('video')]
}

function stopVideos(): void {
  for (const video of collectVideos()) {
    video.pause()
    video.removeAttribute('src')
    video.load()
  }
}

async function playVideos(): Promise<void> {
  await nextTick()
  await Promise.all(collectVideos().map(async (video) => {
    try {
      await video.play()
    } catch {
      // muted autoplay should succeed; ignore browser policy failures
    }
  }))
}

function markFailed(cameraId: string): void {
  failedSources.value = { ...failedSources.value, [cameraId]: true }
}

onMounted(() => {
  void playVideos()
})

onBeforeUnmount(() => {
  stopVideos()
})
</script>

<template>
  <section
    ref="panelRef"
    class="roadside-device-panel"
    aria-label="路侧设备画面"
  >
    <header class="roadside-device-panel__header">
      <div>
        <strong>路侧设备画面</strong>
        <span>CARLA 预生成序列回放</span>
      </div>
      <button type="button" title="关闭" aria-label="关闭路侧设备画面" @click="emit('close')">×</button>
    </header>

    <div class="roadside-device-panel__cameras">
      <figure
        v-for="camera in CAMERAS"
        :key="camera.id"
        class="roadside-device-panel__camera"
      >
        <figcaption>{{ camera.title }}</figcaption>
        <div class="roadside-device-panel__screen">
          <video
            :src="camera.src"
            autoplay
            muted
            loop
            playsinline
            preload="metadata"
            disablepictureinpicture
            @error="markFailed(camera.id)"
          />
          <p v-if="failedSources[camera.id]" class="roadside-device-panel__empty">
            画面未就绪，请先生成 {{ camera.id }}.mp4
          </p>
        </div>
      </figure>
    </div>
  </section>
</template>

<style scoped>
.roadside-device-panel {
  position: relative;
  width: 100%;
  padding: 10px 12px 10px;
  border: 1px solid rgba(91, 159, 255, .72);
  clip-path: polygon(14px 0, calc(100% - 14px) 0, 100% 14px, 100% calc(100% - 14px), calc(100% - 14px) 100%, 14px 100%, 0 calc(100% - 14px), 0 14px);
  background: linear-gradient(180deg, rgba(20, 48, 89, .97), rgba(8, 35, 72, .97));
  box-shadow: inset 0 0 42px rgba(69, 136, 225, .18), 0 0 26px rgba(18, 110, 218, .24);
  color: #f4fbff;
  pointer-events: auto;
}

.roadside-device-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.roadside-device-panel__header > div {
  display: flex;
  align-items: baseline;
  gap: 14px;
  min-width: 0;
}

.roadside-device-panel__header strong {
  font-size: 16px;
  letter-spacing: 1px;
}

.roadside-device-panel__header span {
  color: #65e8ff;
  font-size: 12px;
  white-space: nowrap;
}

.roadside-device-panel__header button {
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid rgba(98, 216, 255, .45);
  border-radius: 50%;
  background: rgba(2, 21, 44, .72);
  color: #ccefff;
  font-size: 21px;
  cursor: pointer;
}

.roadside-device-panel__cameras {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.roadside-device-panel__camera {
  min-width: 0;
  margin: 0;
}

.roadside-device-panel__camera figcaption {
  margin: 0 0 6px;
  color: #d7f4ff;
  font-size: 12px;
  letter-spacing: .04em;
  text-shadow: 0 0 8px rgba(49, 190, 255, .35);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.roadside-device-panel__screen {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid rgba(91, 159, 255, .72);
  background: #03101f;
  box-shadow:
    inset 0 0 18px rgba(69, 136, 225, .18),
    0 0 12px rgba(18, 110, 218, .2);
}

.roadside-device-panel__screen video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #020b16;
}

.roadside-device-panel__empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  margin: 0;
  padding: 12px;
  color: #8fb1c8;
  font-size: 13px;
  text-align: center;
  background: rgba(2, 12, 28, .72);
}

@media (max-width: 1440px) {
  .roadside-device-panel {
    padding: 8px 10px 8px;
  }

  .roadside-device-panel__header strong {
    font-size: 15px;
  }

  .roadside-device-panel__cameras {
    gap: 6px;
  }

  .roadside-device-panel__camera figcaption {
    font-size: 11px;
  }
}
</style>
