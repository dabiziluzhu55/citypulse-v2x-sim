<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import askButton from '../../assets/design/ai-control/ai-panel-ask-button.svg?url'
import panelFrame from '../../assets/design/ai-control/ai-panel-frame.svg?url'
import panelGreeting from '../../assets/design/ai-control/ai-panel-greeting.svg?url'
import panelInput from '../../assets/design/ai-control/ai-panel-input.svg?url'
import panelTitle from '../../assets/design/ai-control/ai-panel-title.svg?url'
import { chatWithCopilot } from '../../api/copilot'
import { simulationApiErrorMessage } from '../../api/client'
import type { CopilotChatResponse, CopilotHistoryMessage } from '../../types/copilot'
import type { AIControlStatus } from '../../types/simulation'

const props = withDefaults(defineProps<{
  sessionId: string
  activeEventId?: string | null
  activeScope?: string | null
  aiTakeover?: AIControlStatus | null
}>(), {
  activeEventId: null,
  activeScope: null,
  aiTakeover: null,
})

const emit = defineEmits<{
  close: []
}>()

interface DisplayMessage extends CopilotHistoryMessage {
  id: number
  failed?: boolean
}

const question = ref('')
const submitHint = ref('')
const submitting = ref(false)
const messages = ref<DisplayMessage[]>([])
const conversationRef = ref<HTMLElement | null>(null)
const responseMeta = ref<CopilotChatResponse | null>(null)
let messageSequence = 0
let requestController: AbortController | null = null

const canSubmit = computed(() => Boolean(
  props.sessionId.trim() && question.value.trim() && !submitting.value,
))
const takeoverLabel = computed(() => {
  const status = props.aiTakeover
  if (!status?.ai_enabled) return 'AI接管未启用'
  if (status.state === 'ACTIVE') return 'AI接管执行中'
  if (status.state === 'RECOVERING') return 'AI接管恢复中'
  return `AI接管：${status.state}`
})

async function scrollConversationToEnd(): Promise<void> {
  await nextTick()
  const container = conversationRef.value
  if (container) container.scrollTop = container.scrollHeight
}

async function submitQuestion(): Promise<void> {
  const normalizedQuestion = question.value.trim()
  if (!normalizedQuestion) {
    submitHint.value = '请输入需要咨询的交通状态问题'
    return
  }
  if (!props.sessionId.trim()) {
    submitHint.value = '请先启动仿真，再向交通 Copilot 提问'
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
  })
  question.value = ''
  submitHint.value = ''
  responseMeta.value = null
  submitting.value = true
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  void scrollConversationToEnd()

  try {
    const response = await chatWithCopilot(props.sessionId, {
      message: normalizedQuestion,
      history,
      active_event_id: props.activeEventId,
      active_scope: props.activeScope,
    }, controller.signal)
    messages.value.push({
      id: ++messageSequence,
      role: 'assistant',
      content: response.answer,
    })
    responseMeta.value = response
  } catch (cause) {
    if (controller.signal.aborted) return
    const message = simulationApiErrorMessage(cause, 'Traffic Copilot 请求失败')
    messages.value.push({
      id: ++messageSequence,
      role: 'assistant',
      content: message,
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
  responseMeta.value = null
  submitHint.value = ''
})

onBeforeUnmount(() => requestController?.abort())
</script>

<template>
  <section class="ai-control-panel" aria-label="CityPulse-Qwen交通管控大模型">
    <img class="ai-control-panel__frame" :src="panelFrame" alt="" aria-hidden="true" />

    <button
      type="button"
      class="ai-control-panel__close"
      aria-label="关闭AI管控模型"
      title="关闭"
      @click="emit('close')"
    >
      ×
    </button>

    <img
      class="ai-control-panel__title"
      :src="panelTitle"
      alt="CityPulse-Qwen交通管控大模型"
    />
    <img
      class="ai-control-panel__greeting"
      :src="panelGreeting"
      alt="上午好，可对交通状态进行提问"
    />

    <form class="ai-control-panel__question" @submit.prevent="submitQuestion">
      <img
        class="ai-control-panel__input-background"
        :src="panelInput"
        alt=""
        aria-hidden="true"
      />
      <textarea
        v-model="question"
        maxlength="4000"
        :disabled="submitting"
        aria-label="请输入交通状态问题"
        placeholder="请输入需要咨询的交通状态问题"
        @input="submitHint = ''"
        @keydown.ctrl.enter.prevent="submitQuestion"
        @keydown.meta.enter.prevent="submitQuestion"
      />
      <button
        type="submit"
        class="ai-control-panel__ask-button"
        :disabled="!canSubmit"
        :aria-busy="submitting"
        aria-label="问一问"
      >
        <img :src="askButton" alt="问一问" />
      </button>
    </form>

    <div class="ai-control-panel__runtime-status" :class="{ 'is-active': aiTakeover?.ai_enabled }">
      <i aria-hidden="true" />
      <span>{{ takeoverLabel }}</span>
      <span v-if="aiTakeover?.baseline_controller">基线：{{ aiTakeover.baseline_controller }}</span>
    </div>

    <div ref="conversationRef" class="ai-control-panel__conversation" aria-live="polite">
      <p v-if="messages.length === 0" class="ai-control-panel__empty">
        {{ sessionId ? '可询问当前路口、拥堵原因、事件与历史趋势' : '请先启动仿真，再向交通 Copilot 提问' }}
      </p>
      <article
        v-for="message in messages"
        :key="message.id"
        class="ai-control-panel__message"
        :class="[`is-${message.role}`, { 'is-failed': message.failed }]"
      >
        <strong>{{ message.role === 'user' ? '我' : 'CityPulse-Qwen' }}</strong>
        <p>{{ message.content }}</p>
      </article>
      <p v-if="submitting" class="ai-control-panel__thinking">正在分析当前仿真交通状态…</p>
    </div>

    <p v-if="submitHint" class="ai-control-panel__hint" role="status">{{ submitHint }}</p>
    <p v-else-if="responseMeta" class="ai-control-panel__hint">
      {{ responseMeta.model ?? 'Qwen' }} · {{ responseMeta.rounds }}轮 ·
      {{ responseMeta.tool_calls.length }}次工具调用 ·
      {{ responseMeta.latency_ms == null ? '--' : Math.round(responseMeta.latency_ms) }}ms
    </p>
  </section>
</template>

<style scoped>
.ai-control-panel {
  position: relative;
  width: min(930px, calc(100vw - 64px), 105vh);
  aspect-ratio: 930 / 601;
  color: #edf8ff;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  pointer-events: auto;
}

.ai-control-panel__frame {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.ai-control-panel__close {
  position: absolute;
  top: 4.6%;
  right: 3.2%;
  z-index: 5;
  display: grid;
  place-items: center;
  width: clamp(26px, 3.2vw, 34px);
  height: clamp(26px, 3.2vw, 34px);
  padding: 0;
  border: 1px solid rgba(82, 194, 250, .55);
  border-radius: 50%;
  background: rgba(2, 21, 44, .72);
  color: #d9f5ff;
  font-size: clamp(18px, 2vw, 24px);
  line-height: 1;
  cursor: pointer;
}

.ai-control-panel__close:hover,
.ai-control-panel__close:focus-visible {
  border-color: #62d8ff;
  box-shadow: 0 0 12px rgba(33, 230, 255, .55);
  outline: none;
}

.ai-control-panel__title {
  position: absolute;
  top: 4.6%;
  left: 50%;
  width: 37.2%;
  height: auto;
  transform: translateX(-50%);
}

.ai-control-panel__greeting {
  position: absolute;
  top: 19%;
  left: 50%;
  width: 37.96%;
  height: auto;
  transform: translateX(-50%);
}

.ai-control-panel__question {
  position: absolute;
  top: 38.8%;
  left: 50%;
  width: 69.14%;
  height: 18.3%;
  transform: translateX(-50%);
}

.ai-control-panel__input-background {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.ai-control-panel__question textarea {
  position: absolute;
  inset: 0;
  z-index: 1;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  padding:
    clamp(12px, 1.8vw, 20px)
    clamp(88px, 12vw, 122px)
    clamp(40px, 5vw, 50px)
    clamp(16px, 2.2vw, 24px);
  resize: none;
  border: 0;
  outline: 0;
  background: transparent;
  color: #f1f9ff;
  font: 600 clamp(13px, 1.5vw, 17px) / 1.65 inherit;
  caret-color: #21e6ff;
}

.ai-control-panel__question textarea::placeholder { color: rgba(224, 240, 255, .54); }
.ai-control-panel__question textarea:focus { filter: drop-shadow(0 0 5px rgba(33, 230, 255, .25)); }

.ai-control-panel__ask-button {
  position: absolute;
  right: 2.8%;
  bottom: 8.5%;
  z-index: 2;
  width: 13.69%;
  aspect-ratio: 88 / 42;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.ai-control-panel__ask-button img { display: block; width: 100%; height: 100%; }
.ai-control-panel__ask-button:disabled {
  cursor: not-allowed;
  filter: grayscale(.45) brightness(.7);
  opacity: .7;
}
.ai-control-panel__ask-button:hover,
.ai-control-panel__ask-button:focus-visible {
  filter: brightness(1.16) drop-shadow(0 0 8px #21e6ff);
  outline: none;
  transform: translateY(-1px);
}

.ai-control-panel__ask-button:disabled:hover {
  filter: grayscale(.45) brightness(.7);
  transform: none;
}

.ai-control-panel__runtime-status {
  position: absolute;
  top: 58.2%;
  left: 15.5%;
  right: 15.5%;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #8dbbd0;
  font-size: clamp(10px, 1vw, 12px);
}

.ai-control-panel__runtime-status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #607d8b;
  box-shadow: 0 0 7px rgba(96, 125, 139, .65);
}

.ai-control-panel__runtime-status.is-active { color: #75f2b1; }
.ai-control-panel__runtime-status.is-active i {
  background: #13ce66;
  box-shadow: 0 0 9px rgba(19, 206, 102, .8);
}

.ai-control-panel__conversation {
  position: absolute;
  top: 62%;
  left: 15.5%;
  right: 15.5%;
  bottom: 8%;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 9px;
  overflow: auto;
  padding: 4px 8px 10px;
  scrollbar-color: rgba(82, 194, 250, .55) transparent;
  scrollbar-width: thin;
}

.ai-control-panel__empty,
.ai-control-panel__thinking {
  margin: auto;
  color: rgba(198, 225, 239, .7);
  font-size: clamp(11px, 1.15vw, 14px);
  text-align: center;
}

.ai-control-panel__thinking {
  margin: 0;
  padding: 8px 12px;
  text-align: left;
}

.ai-control-panel__message {
  max-width: 82%;
  padding: 8px 12px;
  border: 1px solid rgba(82, 194, 250, .24);
  border-radius: 9px;
  background: rgba(9, 49, 91, .58);
  box-shadow: inset 0 0 12px rgba(33, 230, 255, .06);
}

.ai-control-panel__message.is-user {
  align-self: flex-end;
  background: rgba(35, 91, 163, .58);
}

.ai-control-panel__message.is-failed {
  border-color: rgba(255, 107, 107, .46);
  color: #ffb4b4;
}

.ai-control-panel__message strong {
  display: block;
  margin-bottom: 3px;
  color: #65d9ff;
  font-size: clamp(10px, 1vw, 12px);
}

.ai-control-panel__message p {
  margin: 0;
  color: inherit;
  font-size: clamp(11px, 1.15vw, 14px);
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ai-control-panel__hint {
  position: absolute;
  top: 54.8%;
  left: 50%;
  width: 69%;
  margin: 0;
  transform: translateX(-50%);
  color: #8fd9f7;
  font-size: clamp(11px, 1.2vw, 14px);
  text-align: center;
}

@media (max-width: 720px) {
  .ai-control-panel { width: calc(100vw - 24px); }
  .ai-control-panel__close { top: 3%; right: 2%; }
}

@media (prefers-reduced-motion: reduce) {
  .ai-control-panel__ask-button,
  .ai-control-panel__close { transition: none; }
}
</style>
