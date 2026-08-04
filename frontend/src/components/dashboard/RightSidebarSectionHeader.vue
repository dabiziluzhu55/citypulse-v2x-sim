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
          x1="32"
          y1="12"
          x2="274"
          y2="12"
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
          x1="166"
          y1="6"
          x2="364"
          y2="6"
          gradientUnits="userSpaceOnUse"
        >
          <stop stop-color="#5575E2" stop-opacity="0.55" />
          <stop offset="1" stop-color="#5078E4" stop-opacity="0.07" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-stripe-${uid}`"
          x1="305"
          y1="24"
          x2="306"
          y2="36"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0.116" stop-color="#5D7BFB" stop-opacity="0" />
          <stop offset="1" stop-color="#5D7BFB" stop-opacity="0.56" />
        </linearGradient>
        <linearGradient
          :id="`rs-sh-bottom-${uid}`"
          x1="0"
          y1="40"
          x2="368"
          y2="40"
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
          <rect x="1" y="0" width="158.732" :height="layout.height" fill="#D9D9D9" />
        </mask>
      </defs>

      <path
        d="M159.763 0H7.215L0 8.235V39.024H368V22.955L357.692 11.478H169.554L159.763 0Z"
        fill="#1D3373"
        fill-opacity="0.64"
      />
      <path
        d="M159.763 0H7.215L0 8.235V39.024H368V22.955L357.692 11.478H169.554L159.763 0Z"
        :fill="`url(#rs-sh-fill-${uid})`"
      />

      <g :filter="`url(#rs-sh-cyan-glow-${uid})`">
        <path
          d="M9.1 2.295H159.908L169.7 13.773H357.839L366.939 23.906L369 22.955L358.693 11.478H170.585L160.793 0H8.246L1.031 8.034L3.092 8.985L9.1 2.295Z"
          fill="#60B7FC"
          fill-opacity="0.47"
          shape-rendering="crispEdges"
        />
        <path
          d="M9.1 2.295H159.908L169.7 13.773H357.839L366.939 23.906L369 22.955L358.693 11.478H170.585L160.793 0H8.246L1.031 8.034L3.092 8.985L9.1 2.295Z"
          :fill="`url(#rs-sh-edge-${uid})`"
          shape-rendering="crispEdges"
        />
      </g>

      <g :mask="`url(#rs-sh-halo-mask-${uid})`">
        <ellipse
          cx="80"
          cy="1.3"
          rx="42.8"
          ry="21.2"
          fill="#3B5DD4"
          fill-opacity="0.49"
          :filter="`url(#rs-sh-halo-lg-${uid})`"
        />
        <ellipse
          cx="80"
          cy="1.3"
          rx="17"
          ry="9.3"
          fill="#3B5DD4"
          fill-opacity="0.99"
          :filter="`url(#rs-sh-halo-sm-${uid})`"
        />
      </g>

      <rect x="0" y="38.8" width="368" height="1.15" :fill="`url(#rs-sh-bottom-${uid})`" />

      <path
        d="M166.837 0H364.74L369.877 6.619H171.991L166.837 0Z"
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
