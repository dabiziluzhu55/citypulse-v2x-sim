<script setup lang="ts">
import { ref } from 'vue'

import askButton from '../../assets/design/ai-control/ai-panel-ask-button.svg?url'
import panelFrame from '../../assets/design/ai-control/ai-panel-frame.svg?url'
import panelGreeting from '../../assets/design/ai-control/ai-panel-greeting.svg?url'
import panelInput from '../../assets/design/ai-control/ai-panel-input.svg?url'
import panelTitle from '../../assets/design/ai-control/ai-panel-title.svg?url'

const emit = defineEmits<{
  close: []
  submit: [question: string]
}>()

const question = ref('')
const submitHint = ref('')

function submitQuestion(): void {
  const normalizedQuestion = question.value.trim()
  if (!normalizedQuestion) {
    submitHint.value = '请输入需要咨询的交通状态问题'
    return
  }

  emit('submit', normalizedQuestion)
  submitHint.value = 'AI 管控接口暂未接入，当前仅展示界面交互'
}
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
        maxlength="500"
        aria-label="请输入交通状态问题"
        placeholder="请输入需要咨询的交通状态问题"
        @input="submitHint = ''"
        @keydown.ctrl.enter.prevent="submitQuestion"
        @keydown.meta.enter.prevent="submitQuestion"
      />
      <button type="submit" class="ai-control-panel__ask-button" aria-label="问一问">
        <img :src="askButton" alt="问一问" />
      </button>
    </form>

    <p v-if="submitHint" class="ai-control-panel__hint" role="status">
      {{ submitHint }}
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
.ai-control-panel__ask-button:hover,
.ai-control-panel__ask-button:focus-visible {
  filter: brightness(1.16) drop-shadow(0 0 8px #21e6ff);
  outline: none;
  transform: translateY(-1px);
}

.ai-control-panel__hint {
  position: absolute;
  top: 59.5%;
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
