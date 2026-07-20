<script setup lang="ts">
import { useId } from 'vue'
import {
  LEFT_SIDEBAR_DESIGN_HEIGHT,
  LEFT_SIDEBAR_DESIGN_WIDTH,
  LEFT_SIDEBAR_SHELL,
} from '../../constants/leftSidebarLayout'

const { innerScreen } = LEFT_SIDEBAR_SHELL
const uid = useId().replace(/:/g, '')
const svgId = (name: string) => `ls-frame-${name}-${uid}`
</script>

<template>
  <svg
    class="left-sidebar-frame"
    :viewBox="`0 0 ${LEFT_SIDEBAR_DESIGN_WIDTH} ${LEFT_SIDEBAR_DESIGN_HEIGHT}`"
    preserveAspectRatio="xMidYMid meet"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <linearGradient :id="svgId('band')" x1="202" y1="0" x2="202" y2="870" gradientUnits="userSpaceOnUse">
        <stop stop-color="#4157AB" stop-opacity="0.55" />
        <stop offset="0.77" stop-color="#4156AD" stop-opacity="0.43" />
        <stop offset="1" stop-color="#4157AB" stop-opacity="0" />
      </linearGradient>
      <linearGradient :id="svgId('top')" x1="130" y1="28" x2="29" y2="111" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B6DFFF" stop-opacity="0" />
        <stop offset="0.48" stop-color="#B6DFFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B6DFFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient :id="svgId('mid')" x1="57" y1="509" x2="57" y2="669" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B4DEFF" stop-opacity="0" />
        <stop offset="0.46" stop-color="#B4DEFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B4DEFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient :id="svgId('bottom')" x1="63" y1="749" x2="63" y2="847" gradientUnits="userSpaceOnUse">
        <stop stop-color="#ACD0FF" stop-opacity="0" />
        <stop offset="0.77" stop-color="#ACD0FF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#ACD0FF" stop-opacity="0" />
      </linearGradient>
      <linearGradient :id="svgId('edge')" x1="223" y1="5" x2="223" y2="852" gradientUnits="userSpaceOnUse">
        <stop stop-color="#6782FF" stop-opacity="0.18" />
        <stop offset="0.38" stop-color="#6581FC" stop-opacity="0.46" />
        <stop offset="0.72" stop-color="#6581FC" stop-opacity="0" />
      </linearGradient>
      <linearGradient :id="svgId('right-rail')" x1="415" y1="251" x2="415" y2="413" gradientUnits="userSpaceOnUse">
        <stop stop-color="#5E8CE5" />
        <stop offset="1" stop-color="#4B66D7" />
      </linearGradient>
      <linearGradient :id="svgId('left-rail')" x1="7" y1="538" x2="7" y2="633" gradientUnits="userSpaceOnUse">
        <stop stop-color="#4A88F7" />
        <stop offset="1" stop-color="#4063F4" />
      </linearGradient>
      <pattern :id="svgId('grid')" width="12" height="12" patternUnits="userSpaceOnUse">
        <path d="M0 0H12M0 0V12" stroke="#7685BC" stroke-opacity="0.17" stroke-width="1" />
      </pattern>
      <filter :id="svgId('glow')" x="-4%" y="-3%" width="108%" height="106%">
        <feDropShadow dx="0" dy="0" stdDeviation="2.4" flood-color="#0DE0FF" flood-opacity="0.48" />
      </filter>
    </defs>

    <path d="M399.15 5L395.359 0H25.727L3 23.448V533.877L14.373 544.877V626.023L3 639.023V869.5H7.739V640.977L19.112 627.977V544.877L7.739 532.5V25.552L27.659 5H399.15Z" :fill="`url(#${svgId('band')})`" />
    <path d="M395.833 3H27.804L6.317 25.672V532.637L17.69 543.5V628L5.843 640.5V828.5L22.902 845L7.739 829.112L8.213 641.5L19.112 630L19.585 542.5L8.686 532.637V27.328L29.374 5.5H398.202L395.833 3Z" fill="#788ABD" fill-opacity="0.38" />
    <path d="M395.833 3H27.804L6.317 25.672V532.637L17.69 543.5V628L5.843 640.5V828.5L22.902 845L7.739 829.112L8.213 641.5L19.112 630L19.585 542.5L8.686 532.637V27.328L29.374 5.5H398.202L395.833 3Z" :fill="`url(#${svgId('top')})`" />
    <path d="M395.833 3H27.804L6.317 25.672V532.637L17.69 543.5V628L5.843 640.5V828.5L22.902 845L7.739 829.112L8.213 641.5L19.112 630L19.585 542.5L8.686 532.637V27.328L29.374 5.5H398.202L395.833 3Z" :fill="`url(#${svgId('mid')})`" />
    <path d="M395.833 3H27.804L6.317 25.672V532.637L17.69 543.5V628L5.843 640.5V828.5L22.902 845L7.739 829.112L8.213 641.5L19.112 630L19.585 542.5L8.686 532.637V27.328L29.374 5.5H398.202L395.833 3Z" :fill="`url(#${svgId('bottom')})`" />

    <path d="M419.052 26V246L407.68 257V406L419.052 418V829L398.202 851H28.588L7.739 829V641L19.112 629V543L7.739 532V26L28.588 4H398.202L419.052 26Z" fill="#17234B" fill-opacity="var(--sidebar-panel-opacity)" stroke="#5F669F" stroke-opacity="0.43" stroke-width="2" />
    <rect :x="innerScreen.x" :y="innerScreen.y" :width="innerScreen.width" :height="innerScreen.height" :rx="innerScreen.rx" :fill="`url(#${svgId('grid')})`" opacity="0.8" />
    <path d="M397.728 852L418.578 830V419L407.206 407V258L418.578 247V27L397.728 5H328.07" :stroke="`url(#${svgId('edge')})`" stroke-width="2" />

    <g :filter="`url(#${svgId('glow')})`">
      <path d="M411.47 259L419.052 251V413L411.47 405V259Z" :fill="`url(#${svgId('right-rail')})`" />
      <path d="M417.157 253L419.052 251V413L417.157 411V253Z" fill="#73B0F1" fill-opacity="0.94" />
      <path d="M3.948 538L9.634 545V626L3.948 633V538Z" :fill="`url(#${svgId('left-rail')})`" />
      <rect x="8.686" y="545" width="1.895" height="81" fill="#559FF8" fill-opacity="0.48" />
    </g>
  </svg>
</template>

<style scoped>
.left-sidebar-frame {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
