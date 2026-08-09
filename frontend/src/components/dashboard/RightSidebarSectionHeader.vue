<script setup lang="ts">
import { computed, useId } from 'vue'
import {
  RIGHT_SIDEBAR_CONTENT_HEIGHT,
  RIGHT_SIDEBAR_CONTENT_WIDTH,
  RIGHT_SIDEBAR_SECTION_HEADERS,
} from '../../constants/rightSidebarLayout'

const props = defineProps<{
  title: string
  variant: 'communication' | 'metrics'
}>()

const instanceId = useId().replace(/:/g, '')
const uid = computed(() => `${props.variant}-${instanceId}`)
const layout = computed(() => RIGHT_SIDEBAR_SECTION_HEADERS[props.variant])
</script>

<template>
  <div
    class="rs-section-header"
    :class="`rs-section-header--${variant}`"
    :style="{
      left: `calc(${layout.left} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
      top: `calc(${layout.top} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
      width: `calc(${layout.width} / ${RIGHT_SIDEBAR_CONTENT_WIDTH} * 100%)`,
      height: `calc(${layout.height} / ${RIGHT_SIDEBAR_CONTENT_HEIGHT} * 100%)`,
    }"
  >
    <svg
      class="rs-section-header__shape"
      :viewBox="layout.viewBox"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient
          :id="`rs-sh-fill-${uid}`"
          x1="184"
          y1="0"
          x2="184"
          y2="30"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#4C87F7" stop-opacity="0.56" />
          <stop offset="1" stop-color="#4C87F7" stop-opacity="0" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-edge-${uid}`"
          x1="32.325"
          y1="13.235"
          x2="274.55"
          y2="13.235"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#67B7FD" stop-opacity="0.7" />
          <stop offset="0.226" stop-color="#95DEFF" stop-opacity="0.97" />
          <stop offset="0.449" stop-color="#55C2FA" stop-opacity="0.43" />
          <stop offset="0.72" stop-color="#73C0FF" stop-opacity="0.78" />
          <stop offset="1" stop-color="#95DEFF" stop-opacity="0" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-accent-${uid}`"
          x1="166.837"
          y1="6.618"
          x2="364.74"
          y2="6.618"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#5575E2" stop-opacity="0.55" />
          <stop offset="1" stop-color="#5078E4" stop-opacity="0.07" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-stripe-${uid}`"
          x1="305.041"
          y1="25.142"
          x2="306.554"
          y2="36.984"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0.116" stop-color="#5D7BFB" stop-opacity="0" />
          <stop offset="1" stop-color="#5D7BFB" stop-opacity="0.56" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-bottom-${uid}`"
          x1="-0.143"
          y1="45.004"
          x2="367.832"
          y2="45.004"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#557FE9" stop-opacity="0.05" />
          <stop offset="0.377" stop-color="#5375E1" stop-opacity="0.24" />
          <stop offset="1" stop-color="#5571E2" stop-opacity="0.11" />
        </linearGradient>
        <filter
          :id="`rs-sh-cyan-glow-${uid}`"
          x="-5%"
          y="-20%"
          width="110%"
          height="160%"
          filterUnits="objectBoundingBox"
        >
          <feDropShadow dx="0" dy="0" stdDeviation="2" flood-color="#0DE0FF" flood-opacity="0.54" />
        </filter>
        <filter :id="`rs-sh-halo-lg-${uid}`" x="-80%" y="-200%" width="260%" height="500%">
          <feGaussianBlur stdDeviation="10" />
        </filter>
        <filter :id="`rs-sh-halo-sm-${uid}`" x="-100%" y="-200%" width="300%" height="500%">
          <feGaussianBlur stdDeviation="5" />
        </filter>
        <mask
          :id="`rs-sh-halo-mask-${uid}`"
          style="mask-type: alpha"
          maskUnits="userSpaceOnUse"
          x="0"
          y="0"
          width="160"
          :height="layout.height"
        >
          <rect x="0.888" y="0" width="158.734" :height="layout.height" fill="#D9D9D9" />
        </mask>
      </defs>

      <path
        d="M159.776 0H7.216L0 8.235V40H368V23.529L357.692 11.765H169.569L159.776 0Z"
        fill="#1D3373"
        fill-opacity="0.64"
      />
      <path
        d="M159.776 0H7.216L0 8.235V40H368V23.529L357.692 11.765H169.569L159.776 0Z"
        :fill="`url(#rs-sh-fill-${uid})`"
      />

      <g :filter="`url(#rs-sh-cyan-glow-${uid})`">
        <path
          d="M8.957 2.647H159.768L169.56 15.882H357.701L366.801 27.567L368.863 26.471L358.555 13.235H170.445L160.653 0H8.103L0.888 9.265L2.949 10.361L8.957 2.647Z"
          fill="#60B7FC"
          fill-opacity="0.47"
          shape-rendering="crispEdges"
        />
        <path
          d="M8.957 2.647H159.768L169.56 15.882H357.701L366.801 27.567L368.863 26.471L358.555 13.235H170.445L160.653 0H8.103L0.888 9.265L2.949 10.361L8.957 2.647Z"
          :fill="`url(#rs-sh-edge-${uid})`"
          shape-rendering="crispEdges"
        />
      </g>

      <g :mask="`url(#rs-sh-halo-mask-${uid})`">
        <ellipse
          cx="80.77"
          cy="1.324"
          rx="42.776"
          ry="21.176"
          fill="#3B5DD4"
          fill-opacity="0.49"
          :filter="`url(#rs-sh-halo-lg-${uid})`"
        />
        <ellipse
          cx="80.77"
          cy="1.324"
          rx="17.007"
          ry="9.265"
          fill="#3B5DD4"
          fill-opacity="0.99"
          :filter="`url(#rs-sh-halo-sm-${uid})`"
        />
      </g>

      <rect x="-0.143" y="43.677" width="367.975" height="1.324" :fill="`url(#rs-sh-bottom-${uid})`" />

      <path
        d="M166.837 0H359.586L364.74 6.618H171.991L166.837 0Z"
        :fill="`url(#rs-sh-accent-${uid})`"
      />

      <path d="M251.358 18.529H240.02L253.419 37.059H264.758L251.358 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M271.973 18.529H260.635L274.034 37.059H285.372L271.973 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M281.249 18.529H292.588L306.987 37.059H295.649L281.249 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M313.202 18.529H301.864L315.264 37.059H326.602L313.202 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M322.479 18.529H333.817L347.217 37.059H335.879L322.479 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M354.432 18.529H343.094L356.494 37.059H367.832L354.432 18.529Z" :fill="`url(#rs-sh-stripe-${uid})`" />
      <path d="M360.617 13.235H367.832V22.5L360.617 13.235Z" fill="#FFE47A" />
    </svg>

    <h2 class="rs-section-header__title">{{ title }}</h2>
  </div>
</template>

<style scoped>
.rs-section-header {
  position: absolute;
  z-index: 5;
  pointer-events: none;
}

.rs-section-header__shape {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.rs-section-header__title {
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
</style>
