<script setup lang="ts">
import {
  RIGHT_SIDEBAR_SHELL,
  RIGHT_SIDEBAR_SHELL_SCALE_X,
  RIGHT_SIDEBAR_SHELL_SCALE_Y,
} from '../../constants/rightSidebarLayout'

const shellScale = `scale(${RIGHT_SIDEBAR_SHELL_SCALE_X}, ${RIGHT_SIDEBAR_SHELL_SCALE_Y})`
const { innerScreen } = RIGHT_SIDEBAR_SHELL
</script>

<template>
  <svg
    class="right-sidebar-frame"
    viewBox="0 0 560 990"
    preserveAspectRatio="xMidYMid meet"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="rs-frame-grad-top" x1="176" y1="27.5" x2="267" y2="111" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B6DFFF" stop-opacity="0" />
        <stop offset="0.482" stop-color="#B6DFFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B6DFFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="rs-frame-grad-mid" x1="232" y1="435" x2="232" y2="595" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B4DEFF" stop-opacity="0" />
        <stop offset="0.456" stop-color="#B4DEFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B4DEFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="rs-frame-grad-bottom" x1="232" y1="748" x2="232" y2="847" gradientUnits="userSpaceOnUse">
        <stop stop-color="#ACD0FF" stop-opacity="0" />
        <stop offset="0.774" stop-color="#ACD0FF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#ACD0FF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="rs-frame-edge-glow" x1="232" y1="5" x2="232" y2="852" gradientUnits="userSpaceOnUse">
        <stop stop-color="#6782FF" stop-opacity="0.18" />
        <stop offset="0.171" stop-color="#6987FF" stop-opacity="0.07" />
        <stop offset="0.381" stop-color="#6581FC" stop-opacity="0.46" />
        <stop offset="0.72" stop-color="#6581FC" stop-opacity="0" />
        <stop offset="1" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="rs-frame-left-tab" x1="14.77" y1="259" x2="14.77" y2="413" gradientUnits="userSpaceOnUse">
        <stop stop-color="#5E8CE5" />
        <stop offset="1" stop-color="#4B66D7" />
      </linearGradient>
      <linearGradient id="rs-frame-left-tab-edge" x1="11.86" y1="253" x2="11.86" y2="411" gradientUnits="userSpaceOnUse">
        <stop stop-color="#73B0F1" stop-opacity="0.94" />
        <stop offset="0.307" stop-color="#6DA7EF" stop-opacity="0.94" />
        <stop offset="0.778" stop-color="#5E8FE5" stop-opacity="0.94" />
        <stop offset="1" stop-color="#5C8AE5" stop-opacity="0.94" />
      </linearGradient>
      <linearGradient id="rs-frame-right-tab" x1="426.18" y1="538" x2="426.18" y2="633" gradientUnits="userSpaceOnUse">
        <stop stop-color="#4A88F7" />
        <stop offset="1" stop-color="#4063F4" />
      </linearGradient>
      <linearGradient id="rs-frame-right-tab-edge" x1="423.27" y1="545" x2="423.27" y2="626" gradientUnits="userSpaceOnUse">
        <stop stop-color="#559FF8" stop-opacity="0.46" />
        <stop offset="1" stop-color="#4A88F8" stop-opacity="0.48" />
      </linearGradient>
      <linearGradient id="rs-frame-panel-fill" x1="359" y1="67" x2="43" y2="438" gradientUnits="userSpaceOnUse">
        <stop stop-color="#D9D9D9" />
        <stop offset="0.628" stop-color="#D9D9D9" stop-opacity="0" />
      </linearGradient>
      <filter id="rs-frame-shell-glow" x="-3%" y="-3%" width="106%" height="106%">
        <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#0DE0FF" flood-opacity="0.54" />
      </filter>
      <clipPath id="rs-frame-panel-clip">
        <path
          d="M10.9548 26V246L22.4089 257V406L10.9548 418V829L31.9548 851H404.227L425.227 829V641L413.773 629V543L425.227 532V26L404.227 4H31.9548L10.9548 26Z"
        />
      </clipPath>
    </defs>

    <g :transform="shellScale">
    <!-- 内框多层渐变底 -->
    <path
      d="M34.3409 3H405.018L426.659 25.6716V532.637L415.205 543.5V628L427.136 640.5V828.5L426.182 829.403L410.586 845.75L409.955 845L425.227 829.112V828.681L424.75 641.5L413.773 630L413.295 542.5L424.273 532.637V27.3284L403.436 5.5H31.9545L34.3409 3Z"
      fill="#788ABD"
      fill-opacity="var(--sidebar-frame-band-opacity)"
    />
    <path
      d="M34.3409 3H405.018L426.659 25.6716V532.637L415.205 543.5V628L427.136 640.5V828.5L426.182 829.403L410.586 845.75L409.955 845L425.227 829.112V828.681L424.75 641.5L413.773 630L413.295 542.5L424.273 532.637V27.3284L403.436 5.5H31.9545L34.3409 3Z"
      fill="url(#rs-frame-grad-top)"
    />
    <path
      d="M34.3409 3H405.018L426.659 25.6716V532.637L415.205 543.5V628L427.136 640.5V828.5L426.182 829.403L410.586 845.75L409.955 845L425.227 829.112V828.681L424.75 641.5L413.773 630L413.295 542.5L424.273 532.637V27.3284L403.436 5.5H31.9545L34.3409 3Z"
      fill="url(#rs-frame-grad-mid)"
    />
    <path
      d="M34.3409 3H405.018L426.659 25.6716V532.637L415.205 543.5V628L427.136 640.5V828.5L426.182 829.403L410.586 845.75L409.955 845L425.227 829.112V828.681L424.75 641.5L413.773 630L413.295 542.5L424.273 532.637V27.3284L403.436 5.5H31.9545L34.3409 3Z"
      fill="url(#rs-frame-grad-bottom)"
    />

    <!-- 主面板缺口轮廓 -->
    <path
      d="M10.9548 26V246L22.4089 257V406L10.9548 418V829L31.9548 851H404.227L425.227 829V641L413.773 629V543L425.227 532V26L404.227 4H31.9548L10.9548 26Z"
      fill="#17234B"
      fill-opacity="var(--sidebar-panel-opacity)"
    />

    <!-- 内屏底色（裁剪至主面板内腔，避免底部斜角溢出） -->
    <g clip-path="url(#rs-frame-panel-clip)">
    <rect
      :x="innerScreen.x"
      :y="innerScreen.y"
      :width="innerScreen.width"
      :height="innerScreen.height"
      :rx="innerScreen.rx"
      fill="var(--sidebar-inner-fill)"
      stroke="#5F669F"
      stroke-opacity="var(--sidebar-inner-stroke-opacity)"
    />
    <rect
      :x="innerScreen.x"
      :y="innerScreen.y"
      :width="innerScreen.width"
      :height="innerScreen.height"
      :rx="innerScreen.rx"
      fill="url(#rs-frame-panel-fill)"
      fill-opacity="var(--sidebar-inner-highlight-opacity)"
    />
    </g>

    <!-- 内描边加强 -->
    <path
      d="M10.9548 26V246L22.4089 257V406L10.9548 418V829L31.9548 851H404.227L425.227 829V641L413.773 629V543L425.227 532V26L404.227 4H31.9548L10.9548 26Z"
      fill="none"
      stroke="#5F669F"
      stroke-opacity="0.43"
      stroke-width="2"
    />

    <!-- 左侧竖向高光 -->
    <path
      d="M32.4319 852L11.4319 830V419L22.8864 407V258L11.4319 247V27L32.4319 5H102.591"
      stroke="url(#rs-frame-edge-glow)"
      stroke-width="2"
    />

    <!-- 左侧结构凸台 -->
    <path d="M18.5909 259L10.9546 251V413L18.5909 405V259Z" fill="url(#rs-frame-left-tab)" />
    <path d="M12.8636 253L10.9546 251V413L12.8636 411V253Z" fill="url(#rs-frame-left-tab-edge)" />

    <!-- 右侧结构凸台 -->
    <path d="M429.045 538L423.318 545V626L429.045 633V538Z" fill="url(#rs-frame-right-tab)" />
    <rect width="1.909" height="81" transform="matrix(-1 0 0 1 424.273 545)" fill="url(#rs-frame-right-tab-edge)" />

    <!-- 外轮廓青色发光 -->
    <g filter="url(#rs-frame-shell-glow)">
      <path
        d="M10.9548 26V246L22.4089 257V406L10.9548 418V829L31.9548 851H404.227L425.227 829V641L413.773 629V543L425.227 532V26L404.227 4H31.9548L10.9548 26Z"
        fill="none"
        stroke="#52C2FA"
        stroke-opacity="0.55"
        stroke-width="2"
      />
    </g>
    </g>
  </svg>
</template>

<style scoped>
.right-sidebar-frame {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
