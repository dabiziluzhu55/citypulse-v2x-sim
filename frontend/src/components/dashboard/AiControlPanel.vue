<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { chatWithCopilot } from '../../api/copilot'
import { simulationApiErrorMessage } from '../../api/client'
import {
  CITYPULSE_QWEN_BENCHMARK_METRICS,
} from '../../constants/citypulseQwenBenchmark'
import type { CopilotHistoryMessage } from '../../types/copilot'
import type { AIControlStatus } from '../../types/simulation'
import {
  formatIntersectionLabel,
  formatIntersectionReferences,
} from '../../utils/intersectionLabels'
import borderSvg from '../../assets/design/dashboard/border.svg?url'
import improveIconSvg from '../../assets/design/dashboard/improve_icon.svg?url'
import baseSvg from '../../assets/design/dashboard/base.svg?url'
import AiOutlineIcon from './AiOutlineIcon.vue'

const props = withDefaults(defineProps<{
  sessionId: string
  activeEventId?: string | null
  activeEventLabel?: string | null
  activeScope?: string | null
  aiTakeover?: AIControlStatus | null
}>(), {
  activeEventId: null,
  activeEventLabel: null,
  activeScope: null,
  aiTakeover: null,
})

const emit = defineEmits<{ close: [] }>()

interface DisplayMessage extends CopilotHistoryMessage {
  id: number
  createdAt: string
  failed?: boolean
}

const question = ref('')
const submitHint = ref('')
const submitting = ref(false)
const messages = ref<DisplayMessage[]>([])
const conversationRef = ref<HTMLElement | null>(null)
let messageSequence = 0
let requestController: AbortController | null = null

const canSubmit = computed(() => Boolean(
  question.value.trim() && !submitting.value,
))

const hasSession = computed(() => Boolean(props.sessionId.trim()))
const aiControlOn = computed(() => Boolean(props.aiTakeover?.ai_enabled))
const welcomeBody = computed(() => {
  if (!hasSession.value) {
    return '当前未开启交通仿真和AI管控，暂无实时仿真数据。\n您仍可以咨询交通控制算法、扰动处置、交通工程知识和一般交通态势问题。'
  }
  if (!aiControlOn.value) {
    return '交通仿真已启动，AI管控未开启。\n您可以查询实时交通状态、预测结果，也可以进行交通知识问答。'
  }
  return '我可以帮您查询交通状态、分析拥堵原因或提供管控建议。'
})
const thinkingLabel = computed(() => (
  hasSession.value ? '正在分析当前仿真交通状态……' : '正在思考……'
))
const benchmarkMetrics = CITYPULSE_QWEN_BENCHMARK_METRICS

type StatusTone = 'idle' | 'planning' | 'executing' | 'failed'

const statusTone = computed<StatusTone>(() => {
  const status = props.aiTakeover
  if (status?.last_error) return 'failed'
  if (!status?.ai_enabled) return 'idle'
  if (status.state === 'ACTIVE') return 'executing'
  if (status.state === 'ARMED' || status.state === 'RECOVERY' || status.state === 'FALLBACK') {
    return 'planning'
  }
  return 'idle'
})

const takeoverLabel = computed(() => {
  const status = props.aiTakeover
  if (status?.last_error) return 'AI接管失败'
  if (!status?.ai_enabled) return 'AI管控未开启'
  if (status.state === 'ACTIVE') return 'AI接管中'
  if (status.state === 'ARMED') return 'AI规划中'
  if (status.state === 'RECOVERY' || status.state === 'FALLBACK') return 'AI恢复中'
  return `AI接管：${status.state}`
})

const statusTarget = computed(() => {
  const status = props.aiTakeover
  if (!status?.ai_enabled) return ''
  const eventLabel = formatIntersectionReferences(
    props.activeEventLabel || props.activeEventId || '',
  ).trim()
  const intersectionLabel = status.controlled_intersections[0]
    ? formatIntersectionLabel(status.controlled_intersections[0])
    : ''
  return [eventLabel, intersectionLabel].filter(Boolean).join(' · ')
})

const statusMeta = computed(() => {
  const status = props.aiTakeover
  if (!status?.ai_enabled) return ''
  const parts: string[] = []
  if (status.plan_sequence > 0) parts.push(`第${status.plan_sequence}轮规划`)
  if (status.controlled_intersections.length > 0) {
    parts.push(`受控${status.controlled_intersections.length}个路口`)
  }
  return parts.join(' · ')
})

function formatBenchmarkPercent(value: number): string {
  return `${value.toFixed(1)}%`
}

function messageTime(): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date())
}

async function scrollConversationToEnd(): Promise<void> {
  await nextTick()
  const container = conversationRef.value
  if (container) container.scrollTop = container.scrollHeight
}

async function submitQuestion(): Promise<void> {
  const normalizedQuestion = question.value.trim()
  if (!normalizedQuestion) {
    submitHint.value = '请输入需要咨询的交通问题'
    return
  }
  if (submitting.value) return

  const history = messages.value
    .filter((item) => !item.failed)
    .map(({ role, content }) => ({ role, content }))
    .slice(-20)
  messages.value.push({
    id: ++messageSequence,
    role: 'user',
    content: normalizedQuestion,
    createdAt: messageTime(),
  })
  question.value = ''
  submitHint.value = ''
  submitting.value = true
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  void scrollConversationToEnd()

  try {
    const response = await chatWithCopilot({
      message: normalizedQuestion,
      history,
      active_event_id: props.activeEventId,
      active_scope: props.activeScope,
    }, props.sessionId, controller.signal)
    messages.value.push({
      id: ++messageSequence,
      role: 'assistant',
      content: formatIntersectionReferences(response.answer),
      createdAt: messageTime(),
    })
  } catch (cause) {
    if (controller.signal.aborted) return
    const message = simulationApiErrorMessage(cause, 'CityPulse-Qwen 请求失败')
    messages.value.push({
      id: ++messageSequence,
      role: 'assistant',
      content: message,
      createdAt: messageTime(),
      failed: true,
    })
    submitHint.value = message
  } finally {
    if (requestController === controller) {
      submitting.value = false
      requestController = null
      void scrollConversationToEnd()
    }
  }
}

watch(() => props.sessionId, () => {
  requestController?.abort()
  requestController = null
  submitting.value = false
  messages.value = []
  submitHint.value = ''
})

onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <section class="ai-control-panel" aria-label="CityPulse-Qwen AI交通助手">
    <header class="ai-control-panel__header">
      <span class="ai-control-panel__avatar" aria-hidden="true">
        <AiOutlineIcon />
      </span>
      <div class="ai-control-panel__identity">
        <strong>CityPulse-Qwen AI助手</strong>
      </div>
      <button type="button" class="ai-control-panel__close" aria-label="关闭AI交通助手" title="关闭" @click="emit('close')">
        ×
      </button>
    </header>

    <div class="ai-control-panel__meta">
      <div class="ai-control-panel__runtime-status" :class="`is-${statusTone}`">
        <i aria-hidden="true" />
        <div class="ai-control-panel__status-copy">
          <strong>{{ takeoverLabel }}<template v-if="statusTarget"> · {{ statusTarget }}</template></strong>
          <span v-if="statusMeta">{{ statusMeta }}</span>
        </div>
      </div>

      <section class="ai-control-panel__effect" aria-label="AI管控效能，模型验证结果，相对固定配时">
        <div class="ai-control-panel__effect-heading">
          <h3>AI管控效能</h3>
        </div>
        <div class="ai-control-panel__effect-grid">
          <article
            v-for="item in benchmarkMetrics"
            :key="item.key"
            class="ai-control-panel__effect-card"
          >
            <img
              :src="borderSvg"
              class="ai-control-panel__effect-frame"
              alt=""
              aria-hidden="true"
              draggable="false"
            >
            <div class="ai-control-panel__effect-content">
              <img
                :src="improveIconSvg"
                class="ai-control-panel__effect-icon"
                alt=""
                aria-hidden="true"
                draggable="false"
              >
              <span class="ai-control-panel__effect-label">{{ item.label }}</span>
              <strong class="ai-control-panel__effect-value">
                <em>{{ item.direction === 'up' ? '↑' : '↓' }}</em>
                <span>{{ formatBenchmarkPercent(item.value) }}</span>
              </strong>
            </div>
          </article>
        </div>
        <img
          :src="baseSvg"
          class="ai-control-panel__effect-base"
          alt=""
          aria-hidden="true"
          draggable="false"
        >
      </section>
    </div>

    <div ref="conversationRef" class="ai-control-panel__conversation" aria-live="polite">
      <article v-if="messages.length === 0" class="ai-control-panel__message is-assistant is-welcome">
        <span class="ai-control-panel__message-avatar" aria-hidden="true">
          <AiOutlineIcon />
        </span>
        <div class="ai-control-panel__message-body">
          <strong>上午好，有什么可以帮您？</strong>
          <p>{{ welcomeBody }}</p>
        </div>
      </article>

      <article
        v-for="message in messages"
        :key="message.id"
        class="ai-control-panel__message"
        :class="[`is-${message.role}`, { 'is-failed': message.failed }]"
      >
        <span v-if="message.role === 'assistant'" class="ai-control-panel__message-avatar" aria-hidden="true">
          <AiOutlineIcon />
        </span>
        <div class="ai-control-panel__message-body">
          <div class="ai-control-panel__message-content"><p>{{ message.content }}</p></div>
          <time>{{ message.createdAt }}</time>
        </div>
      </article>

      <article v-if="submitting" class="ai-control-panel__message is-assistant">
        <span class="ai-control-panel__message-avatar" aria-hidden="true">
          <AiOutlineIcon />
        </span>
        <div class="ai-control-panel__message-body">
          <p class="ai-control-panel__thinking">{{ thinkingLabel }}</p>
        </div>
      </article>
    </div>

    <div class="ai-control-panel__composer">
      <p v-if="submitHint" class="ai-control-panel__hint is-error" role="status">{{ submitHint }}</p>

      <form class="ai-control-panel__question" @submit.prevent="submitQuestion">
        <span class="ai-control-panel__attachment" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none">
            <path d="m8.8 12.9 5.85-5.85a3.1 3.1 0 0 1 4.38 4.38l-7.38 7.38a5 5 0 1 1-7.07-7.07l7.03-7.03" />
          </svg>
        </span>
        <textarea
          v-model="question"
          maxlength="4000"
          :disabled="submitting"
          aria-label="请输入交通问题"
          placeholder="输入您的问题..."
          @input="submitHint = ''"
          @keydown.ctrl.enter.prevent="submitQuestion"
          @keydown.meta.enter.prevent="submitQuestion"
        />
        <button type="submit" class="ai-control-panel__send" :disabled="!canSubmit" :aria-busy="submitting" aria-label="发送问题">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="m4 12 15-7-4.8 14-2.4-5.1L4 12Z" />
            <path d="m11.8 13.9 3.3-3.4" />
          </svg>
        </button>
      </form>
      <p class="ai-control-panel__disclaimer">AI生成内容仅供参考，请结合实际情况决策</p>
    </div>
  </section>
</template>

<style scoped>
.ai-control-panel {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  color: #edf8ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  overflow: hidden;
  overflow-x: hidden;
  border: 1px solid rgba(82, 194, 250, .42);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(8, 48, 94, .70), rgba(3, 29, 66, .70));
  backdrop-filter: blur(6px);
  box-shadow:
    inset 0 0 32px rgba(43, 137, 255, .12),
    0 10px 34px rgba(0, 12, 35, .25);
  pointer-events: none;
}

.ai-control-panel__header {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 65px;
  padding: 17px 16px 11px;
}
.ai-control-panel__avatar,
.ai-control-panel__message-avatar {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 36px;
  height: 36px;
  padding: 2px;
  border: 0;
  background: transparent;
  box-shadow: none;
  color: #55ddff;
}
.ai-control-panel__identity { display: flex; min-width: 0; flex-direction: column; gap: 3px; text-shadow: 0 1px 5px rgba(0,0,0,.82); }
.ai-control-panel__identity strong { overflow: hidden; color: #f3fbff; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.ai-control-panel__identity span { overflow: hidden; color: rgba(205,228,241,.72); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }

.ai-control-panel__close {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 30px;
  height: 30px;
  margin-left: auto;
  padding: 0;
  border: 1px solid rgba(82,194,250,.42);
  border-radius: 50%;
  background: rgba(2,21,44,.38);
  color: #d9f5ff;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  pointer-events: auto;
  transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
}
.ai-control-panel__close:hover,
.ai-control-panel__close:focus-visible { border-color: #62d8ff; background: rgba(12,61,103,.58); box-shadow: 0 0 12px rgba(33,230,255,.5); outline: none; }

.ai-control-panel__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 0 14px 8px;
}

.ai-control-panel__runtime-status {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  box-sizing: border-box;
  min-height: 32px;
  max-height: 40px;
  padding: 2px 4px 6px 6px;
  overflow: hidden;
  color: #8aa4b6;
  font-size: 12px;
  line-height: 1.35;
  text-shadow: 0 1px 4px rgba(0,0,0,.86);
}
.ai-control-panel__runtime-status i {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  margin-top: 6px;
  border-radius: 50%;
  background: #6d8494;
  box-shadow: 0 0 7px rgba(109, 132, 148, .65);
}
.ai-control-panel__status-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}
.ai-control-panel__status-copy strong {
  overflow: hidden;
  color: inherit;
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-control-panel__status-copy span {
  overflow: hidden;
  color: rgba(154, 216, 255, .78);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-control-panel__runtime-status.is-planning { color: #f0ca70; }
.ai-control-panel__runtime-status.is-planning i {
  background: #f0ca70;
  box-shadow: 0 0 9px rgba(240, 202, 112, .8);
}
.ai-control-panel__runtime-status.is-executing { color: #52c2fa; }
.ai-control-panel__runtime-status.is-executing i {
  background: #21e6ff;
  box-shadow: 0 0 9px rgba(33, 230, 255, .8);
}
.ai-control-panel__runtime-status.is-failed { color: #ff8a8a; }
.ai-control-panel__runtime-status.is-failed i {
  background: #ff5b64;
  box-shadow: 0 0 9px rgba(255, 91, 100, .75);
}

.ai-control-panel__effect {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 110px;
  overflow: hidden;
}
.ai-control-panel__effect-heading {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 12px;
  min-width: 0;
  margin-bottom: 8px;
}
.ai-control-panel__effect-heading h3 {
  margin: 0;
  color: #eef8ff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .04em;
  text-shadow: 0 1px 4px rgba(0,0,0,.86);
}
.ai-control-panel__effect-heading span {
  overflow: hidden;
  color: rgba(154, 216, 255, .72);
  font-size: 11px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-control-panel__effect-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  min-width: 0;
  min-height: 0;
  flex: 1;
}
.ai-control-panel__effect-card {
  position: relative;
  min-width: 0;
  min-height: 0;
  height: 70px;
}
.ai-control-panel__effect-frame {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
  user-select: none;
}
.ai-control-panel__effect-content {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-content: center;
  align-items: center;
  column-gap: 4px;
  row-gap: 1px;
  width: 100%;
  height: 100%;
  padding: 7px 6px 6px 6px;
}
.ai-control-panel__effect-icon {
  grid-column: 1;
  grid-row: 1 / span 2;
  display: block;
  width: 22px;
  height: 34px;
  object-fit: contain;
  object-position: center;
  pointer-events: none;
  user-select: none;
}
.ai-control-panel__effect-label {
  grid-column: 2;
  grid-row: 1;
  overflow: hidden;
  color: #accde6;
  font-size: 11px;
  font-weight: 600;
  line-height: 14px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ai-control-panel__effect-value {
  grid-column: 2;
  grid-row: 2;
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  color: #f0ca70;
  font-size: 26px;
  font-weight: 800;
  letter-spacing: .01em;
  line-height: 1;
}
.ai-control-panel__effect-value em {
  font-style: normal;
  font-size: 16px;
  color: #f0ca70;
  filter: drop-shadow(0 0 4px rgba(240, 202, 112, .55));
}
.ai-control-panel__effect-value span {
  background: linear-gradient(180deg, #ffffff 0%, #dffaff 28%, #9aeaff 62%, #56cfff 100%);
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  -webkit-text-stroke: .25px rgba(221, 250, 255, .70);
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: 'tnum' 1, 'lnum' 1;
  font-family: 'Arial Narrow', 'Roboto Condensed', 'DIN Alternate', Bahnschrift, 'Microsoft YaHei', sans-serif;
  filter:
    drop-shadow(0 0 2px rgba(255, 255, 255, .55))
    drop-shadow(0 0 5px rgba(56, 218, 255, .45))
    drop-shadow(0 0 10px rgba(33, 121, 255, .22));
}
.ai-control-panel__effect-base {
  position: absolute;
  left: 50%;
  bottom: -10px;
  z-index: 0;
  width: 118px;
  height: auto;
  transform: translateX(-50%);
  opacity: .55;
  pointer-events: none;
  user-select: none;
}

.ai-control-panel__conversation {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 22px;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 14px 10px 26px;
  mask-image: linear-gradient(to bottom,transparent 0,#000 12px,#000 calc(100% - 10px),transparent 100%);
  scrollbar-color: rgba(82,194,250,.55) transparent;
  scrollbar-width: thin;
  pointer-events: auto;
}
.ai-control-panel__message { display: flex; align-items: flex-start; gap: 12px; width: fit-content; max-width: 82%; }
.ai-control-panel__message.is-welcome { margin-top: 8px; }
.ai-control-panel__message.is-assistant { align-self: flex-start; }
.ai-control-panel__message.is-user { align-self: flex-end; flex-direction: row-reverse; }
.ai-control-panel__message-avatar { width: 32px; height: 32px; margin-top: 2px; padding: 2px; }
.ai-control-panel__message-body { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
.ai-control-panel__message.is-user .ai-control-panel__message-body { align-items: flex-end; }
.ai-control-panel__message-body > strong { margin: 0 0 3px; color: #f4fbff; font-size: 14px; text-shadow: 0 1px 5px rgba(0,0,0,.9); }
.ai-control-panel__message-content { padding: 9px 13px; border-radius: 8px; }
.ai-control-panel__message.is-assistant .ai-control-panel__message-content { padding-left: 0; border: 0; background: transparent; text-shadow: 0 1px 5px rgba(0,0,0,.9); }
.ai-control-panel__message.is-user .ai-control-panel__message-content { border: 1px solid rgba(45,142,255,.52); background: linear-gradient(135deg,rgba(6,91,214,.92),rgba(8,67,167,.88)); box-shadow: 0 4px 18px rgba(0,63,162,.25); }
.ai-control-panel__message p { margin: 0; color: #eef8ff; font-size: 13px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.ai-control-panel__message time { color: rgba(190,216,231,.56); font-size: 10px; text-shadow: 0 1px 4px rgba(0,0,0,.9); }
.ai-control-panel__message.is-user time { text-align: right; }
.ai-control-panel__message.is-failed p { color: #ffabab; }
.ai-control-panel__thinking { padding-top: 5px; color: rgba(198,225,239,.76) !important; text-shadow: 0 1px 5px rgba(0,0,0,.9); }

.ai-control-panel__composer { padding: 0 12px 4px; pointer-events: auto; }
.ai-control-panel__hint { box-sizing: border-box; min-height: 18px; margin: 0 10px 5px; color: #8fd9f7; font-size: 11px; line-height: 1.35; text-shadow: 0 1px 4px rgba(0,0,0,.9); overflow-wrap: anywhere; }
.ai-control-panel__hint.is-error { color: #ff9f9f; }
.ai-control-panel__question {
  display: grid;
  grid-template-columns: 32px minmax(0,1fr) 46px;
  align-items: center;
  min-height: 58px;
  padding: 0 8px 0 10px;
  border: 1px solid rgba(82,194,250,.72);
  border-radius: 18px;
  background: rgba(1,15,31,.38);
  backdrop-filter: blur(8px);
  box-shadow: inset 0 0 18px rgba(33,139,255,.08),0 0 14px rgba(33,139,255,.12);
}
.ai-control-panel__attachment { display: grid; place-items: center; color: rgba(210,233,245,.76); }
.ai-control-panel__attachment svg { width: 19px; height: 19px; }
.ai-control-panel__attachment path,
.ai-control-panel__send path { stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.ai-control-panel__question textarea {
  box-sizing: border-box;
  width: 100%;
  height: 54px;
  padding: 16px 10px 10px;
  resize: none;
  border: 0;
  outline: 0;
  background: transparent;
  color: #f1f9ff;
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.5;
  caret-color: #21e6ff;
}
.ai-control-panel__question textarea::placeholder { color: rgba(211,231,242,.52); }
.ai-control-panel__send {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(145deg,#2ea6ff,#1556d2);
  box-shadow: 0 0 14px rgba(33,139,255,.5);
  color: #fff;
  cursor: pointer;
  transition: filter .18s ease, transform .18s ease, opacity .18s ease;
}
.ai-control-panel__send svg { width: 21px; height: 21px; }
.ai-control-panel__send:hover,
.ai-control-panel__send:focus-visible { filter: brightness(1.15); transform: translateY(-1px); outline: none; }
.ai-control-panel__send:disabled { filter: grayscale(.6); opacity: .5; cursor: not-allowed; transform: none; }
.ai-control-panel__disclaimer { margin: 8px 0 0; color: rgba(178,205,220,.54); font-size: 10px; text-align: center; text-shadow: 0 1px 4px rgba(0,0,0,.9); }

@media (max-width: 900px), (max-height: 760px) {
  .ai-control-panel__effect {
    height: auto;
    max-height: 168px;
  }
  .ai-control-panel__effect-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ai-control-panel__effect-card { height: 64px; }
  .ai-control-panel__effect-value { font-size: 22px; }
}

@media (max-width: 720px) {
  .ai-control-panel__identity span { display: none; }
  .ai-control-panel__status-copy span { display: none; }
  .ai-control-panel__runtime-status { padding-left: 2px; }
  .ai-control-panel__message { max-width: 92%; }
}

@media (prefers-reduced-motion: reduce) {
  .ai-control-panel__send,
  .ai-control-panel__close { transition: none; }
}
</style>
