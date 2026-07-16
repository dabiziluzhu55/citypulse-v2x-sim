<script setup lang="ts">
import { computed } from 'vue'
import {
  LEFT_SIDEBAR_CONTENT_HEIGHT,
  LEFT_SIDEBAR_CONTENT_WIDTH,
  LEFT_SIDEBAR_SECTION_HEADERS,
} from '../../constants/leftSidebarLayout'

const props = defineProps<{
  title: string
  variant: 'scenario' | 'algorithm'
}>()

const uid = computed(() => props.variant)
const layout = computed(() => LEFT_SIDEBAR_SECTION_HEADERS[props.variant])
const isScenario = computed(() => props.variant === 'scenario')
</script>

<template>
  <div
    class="ls-section-header"
    :class="`ls-section-header--${variant}`"
    :style="{
      left: `calc(${layout.left} / ${LEFT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
      top: `calc(${layout.top} / ${LEFT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      width: `calc(${layout.width} / ${LEFT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
      height: `calc(${layout.height} / ${LEFT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
    }"
  >
    <svg
      class="ls-section-header__shape"
      :viewBox="layout.viewBox"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <!-- 主填充渐变 -->
        <linearGradient
          :id="`ls-sh-fill-${uid}`"
          :x1="isScenario ? 184 : 177.076"
          y1="0"
          :x2="isScenario ? 184 : 177.076"
          y2="30"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#4C87F7" stop-opacity="0.56" />
          <stop offset="1" stop-color="#4C87F7" stop-opacity="0" />
        </linearGradient>

        <!-- 边缘高光 -->
        <linearGradient
          :id="`ls-sh-edge-${uid}`"
          :x1="isScenario ? 32.325 : 31.249"
          :y1="isScenario ? 13.235 : 11.765"
          :x2="isScenario ? 274.55 : 264.373"
          :y2="isScenario ? 13.235 : 11.765"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#67B7FD" stop-opacity="0.7" />
          <stop offset="0.226" stop-color="#95DEFF" stop-opacity="0.97" />
          <stop offset="0.449" stop-color="#55C2FA" stop-opacity="0.43" />
          <stop offset="0.72" stop-color="#73C0FF" stop-opacity="0.78" />
          <stop offset="1" stop-color="#95DEFF" stop-opacity="0" />
        </linearGradient>

        <!-- 顶部装饰条 -->
        <linearGradient
          :id="`ls-sh-accent-${uid}`"
          :x1="isScenario ? 166.837 : 160.707"
          :y1="isScenario ? 6.618 : 5.882"
          :x2="isScenario ? 364.74 : 351.175"
          :y2="isScenario ? 6.618 : 5.882"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#5575E2" stop-opacity="0.55" />
          <stop offset="1" stop-color="#5078E4" stop-opacity="0.07" />
        </linearGradient>

        <!-- 斜纹 -->
        <linearGradient
          :id="`ls-sh-stripe-${uid}`"
          :x1="isScenario ? 305.041 : 293.719"
          :y1="isScenario ? 25.142 : 22.348"
          :x2="isScenario ? 306.554 : 294.964"
          :y2="isScenario ? 36.984 : 32.899"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0.116" stop-color="#5D7BFB" stop-opacity="0" />
          <stop offset="1" stop-color="#5D7BFB" stop-opacity="0.56" />
        </linearGradient>

        <!-- 底部分割线 -->
        <linearGradient
          :id="`ls-sh-bottom-${uid}`"
          :x1="isScenario ? -0.143 : 0"
          :y1="isScenario ? 45.004 : 40.003"
          :x2="isScenario ? 367.832 : 354.151"
          :y2="isScenario ? 45.004 : 40.003"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#557FE9" stop-opacity="0.05" />
          <stop offset="0.377" stop-color="#5375E1" stop-opacity="0.24" />
          <stop offset="1" stop-color="#5571E2" stop-opacity="0.11" />
        </linearGradient>

        <!-- 青色外发光 -->
        <filter
          :id="`ls-sh-cyan-glow-${uid}`"
          x="-5%"
          y="-20%"
          width="110%"
          height="160%"
          filterUnits="objectBoundingBox"
        >
          <feDropShadow dx="0" dy="0" stdDeviation="2" flood-color="#0DE0FF" flood-opacity="0.54" />
        </filter>

        <!-- 左侧光晕 -->
        <filter :id="`ls-sh-halo-lg-${uid}`" x="-80%" y="-200%" width="260%" height="500%">
          <feGaussianBlur stdDeviation="10" />
        </filter>
        <filter :id="`ls-sh-halo-sm-${uid}`" x="-100%" y="-200%" width="300%" height="500%">
          <feGaussianBlur stdDeviation="5" />
        </filter>

        <!-- 左侧光晕遮罩 -->
        <mask
          :id="`ls-sh-halo-mask-${uid}`"
          style="mask-type: alpha"
          maskUnits="userSpaceOnUse"
          :x="isScenario ? 0 : 0"
          y="0"
          :width="isScenario ? 160 : 154"
          :height="layout.height"
        >
          <rect
            :x="isScenario ? 0.888 : 0.992"
            y="0"
            :width="isScenario ? 158.734 : 152.771"
            :height="layout.height"
            fill="#D9D9D9"
          />
        </mask>
      </defs>

      <!-- 主底板 -->
      <path
        v-if="isScenario"
        d="M159.776 0H7.216L0 8.235V40H368V23.529L357.692 11.765H169.569L159.776 0Z"
        fill="#1D3373"
        fill-opacity="0.64"
      />
      <path
        v-else
        d="M153.763 0H6.944L0 8.235V40H354.151V23.529L344.231 11.765H163.187L153.763 0Z"
        fill="#1D3373"
        fill-opacity="0.64"
      />

      <path
        v-if="isScenario"
        d="M159.776 0H7.216L0 8.235V40H368V23.529L357.692 11.765H169.569L159.776 0Z"
        :fill="`url(#ls-sh-fill-${uid})`"
      />
      <path
        v-else
        d="M153.763 0H6.944L0 8.235V40H354.151V23.529L344.231 11.765H163.187L153.763 0Z"
        :fill="`url(#ls-sh-fill-${uid})`"
      />

      <!-- 边缘描边层（带青色发光） -->
      <g :filter="`url(#ls-sh-cyan-glow-${uid})`">
        <path
          v-if="isScenario"
          d="M8.957 2.647H159.768L169.56 15.882H357.701L366.801 27.567L368.863 26.471L358.555 13.235H170.445L160.653 0H8.103L0.888 9.265L2.949 10.361L8.957 2.647Z"
          fill="#60B7FC"
          fill-opacity="0.47"
          shape-rendering="crispEdges"
        />
        <path
          v-if="isScenario"
          d="M8.957 2.647H159.768L169.56 15.882H357.701L366.801 27.567L368.863 26.471L358.555 13.235H170.445L160.653 0H8.103L0.888 9.265L2.949 10.361L8.957 2.647Z"
          :fill="`url(#ls-sh-edge-${uid})`"
          shape-rendering="crispEdges"
        />
        <path
          v-if="!isScenario"
          d="M8.758 2.353H153.903L163.328 14.118H344.401L353.159 24.504L355.143 23.529L345.223 11.765H164.179L154.755 0H7.936L0.992 8.235L2.976 9.21L8.758 2.353Z"
          fill="#60B7FC"
          fill-opacity="0.47"
          shape-rendering="crispEdges"
        />
        <path
          v-if="!isScenario"
          d="M8.758 2.353H153.903L163.328 14.118H344.401L353.159 24.504L355.143 23.529L345.223 11.765H164.179L154.755 0H7.936L0.992 8.235L2.976 9.21L8.758 2.353Z"
          :fill="`url(#ls-sh-edge-${uid})`"
          shape-rendering="crispEdges"
        />
      </g>

      <!-- 左侧光晕 -->
      <g :mask="`url(#ls-sh-halo-mask-${uid})`">
        <ellipse
          :cx="isScenario ? 80.77 : 77.874"
          :cy="isScenario ? 1.324 : 1.176"
          :rx="isScenario ? 42.776 : 41.169"
          :ry="isScenario ? 21.176 : 18.824"
          fill="#3B5DD4"
          fill-opacity="0.49"
          :filter="`url(#ls-sh-halo-lg-${uid})`"
        />
        <ellipse
          :cx="isScenario ? 80.77 : 77.874"
          :cy="isScenario ? 1.324 : 1.176"
          :rx="isScenario ? 17.007 : 16.368"
          :ry="isScenario ? 9.265 : 8.235"
          fill="#3B5DD4"
          fill-opacity="0.99"
          :filter="`url(#ls-sh-halo-sm-${uid})`"
        />
      </g>

      <!-- 底部分割线 -->
      <rect
        :x="isScenario ? -0.143 : 0"
        :y="isScenario ? 43.677 : 38.823"
        :width="isScenario ? 367.975 : 354.151"
        :height="isScenario ? 1.324 : 1.176"
        :fill="`url(#ls-sh-bottom-${uid})`"
      />

      <!-- 顶部装饰条 -->
      <path
        v-if="isScenario"
        d="M166.837 0H359.586L364.74 6.618H171.991L166.837 0Z"
        :fill="`url(#ls-sh-accent-${uid})`"
      />
      <path
        v-else
        d="M160.707 0H346.215L351.175 5.882H165.667L160.707 0Z"
        :fill="`url(#ls-sh-accent-${uid})`"
      />

      <!-- 右侧斜纹 -->
      <g>
        <template v-if="isScenario">
          <path d="M251.358 18.529H240.02L253.419 37.059H264.758L251.358 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M271.973 18.529H260.635L274.034 37.059H285.372L271.973 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M281.249 18.529H292.588L306.987 37.059H295.649L281.249 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M313.202 18.529H301.864L315.264 37.059H326.602L313.202 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M322.479 18.529H333.817L347.217 37.059H335.879L322.479 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M354.432 18.529H343.094L356.494 37.059H367.832L354.432 18.529Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M360.617 13.235H367.832V22.5L360.617 13.235Z" fill="#FFE47A" />
        </template>
        <template v-else>
          <path d="M242.053 16.471H230.141L243.037 32.941H254.949L242.053 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M261.893 16.471H250.981L264.877 32.941H276.79L261.893 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M270.821 16.471H282.734L296.63 32.941H284.718L270.821 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M301.574 16.471H290.662L304.558 32.941H315.47L301.574 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M310.502 16.471H321.415L335.311 32.941H323.399L310.502 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M341.255 16.471H330.343L344.239 32.941H356.151L341.255 16.471Z" :fill="`url(#ls-sh-stripe-${uid})`" />
          <path d="M347.207 11.765H354.151V20L347.207 11.765Z" fill="#FFE47A" />
        </template>
      </g>
    </svg>

    <h2 class="ls-section-header__title">{{ title }}</h2>
  </div>
</template>

<style scoped>
.ls-section-header {
  position: absolute;
  z-index: 5;
  pointer-events: none;
}

.ls-section-header__shape {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.ls-section-header__title {
  position: relative;
  z-index: 2;
  margin: 0;
  padding: 0 0 0 14px;
  height: 100%;
  display: flex;
  align-items: center;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #ffffff;
  line-height: 1;
  white-space: nowrap;
  text-shadow:
    0 0 7px rgba(75, 180, 229, 0.37),
    -2px 2px 8px rgba(5, 28, 55, 0.42),
    0 0 14px rgba(49, 190, 255, 0.35);
}

.ls-section-header--algorithm .ls-section-header__title {
  padding-left: 12px;
}
</style>
