<script setup lang="ts">
import {
  LEFT_SIDEBAR_DESIGN_HEIGHT,
  LEFT_SIDEBAR_DESIGN_WIDTH,
  LEFT_SIDEBAR_SHELL,
  LEFT_SIDEBAR_SHELL_SCALE_X,
  LEFT_SIDEBAR_SHELL_SCALE_Y,
} from '../../constants/leftSidebarLayout'

const shellScale = `scale(${LEFT_SIDEBAR_SHELL_SCALE_X}, ${LEFT_SIDEBAR_SHELL_SCALE_Y})`
const { innerScreen } = LEFT_SIDEBAR_SHELL
const frameViewBox = `0 0 ${LEFT_SIDEBAR_DESIGN_WIDTH} ${LEFT_SIDEBAR_DESIGN_HEIGHT}`
</script>

<template>
  <svg
    class="left-sidebar-frame"
    :viewBox="frameViewBox"
    preserveAspectRatio="xMidYMid meet"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="ls-frame-grad-top" x1="127.469" y1="27.5" x2="25.881" y2="111.097" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B6DFFF" stop-opacity="0" />
        <stop offset="0.482" stop-color="#B6DFFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B6DFFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="ls-frame-grad-mid" x1="53.547" y1="508.5" x2="53.547" y2="668.501" gradientUnits="userSpaceOnUse">
        <stop stop-color="#B4DEFF" stop-opacity="0" />
        <stop offset="0.456" stop-color="#B4DEFF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#B4DEFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="ls-frame-grad-bottom" x1="60.181" y1="748.5" x2="60.181" y2="847.001" gradientUnits="userSpaceOnUse">
        <stop stop-color="#ACD0FF" stop-opacity="0" />
        <stop offset="0.774" stop-color="#ACD0FF" stop-opacity="0.73" />
        <stop offset="1" stop-color="#ACD0FF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="ls-frame-edge-glow" x1="220.347" y1="5" x2="220.347" y2="852" gradientUnits="userSpaceOnUse">
        <stop stop-color="#6782FF" stop-opacity="0.18" />
        <stop offset="0.171" stop-color="#6987FF" stop-opacity="0.07" />
        <stop offset="0.381" stop-color="#6581FC" stop-opacity="0.46" />
        <stop offset="0.72" stop-color="#6581FC" stop-opacity="0" />
        <stop offset="1" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="ls-frame-right-tab" x1="412.261" y1="251" x2="412.261" y2="413" gradientUnits="userSpaceOnUse">
        <stop stop-color="#5E8CE5" />
        <stop offset="1" stop-color="#4B66D7" />
      </linearGradient>
      <linearGradient id="ls-frame-right-tab-edge" x1="415.105" y1="253" x2="415.105" y2="411" gradientUnits="userSpaceOnUse">
        <stop stop-color="#73B0F1" stop-opacity="0.94" />
        <stop offset="0.307" stop-color="#6DA7EF" stop-opacity="0.94" />
        <stop offset="0.778" stop-color="#5E8FE5" stop-opacity="0.94" />
        <stop offset="1" stop-color="#5C8AE5" stop-opacity="0.94" />
      </linearGradient>
      <linearGradient id="ls-frame-left-tab" x1="3.791" y1="538" x2="3.791" y2="633" gradientUnits="userSpaceOnUse">
        <stop stop-color="#4A88F7" />
        <stop offset="1" stop-color="#4063F4" />
      </linearGradient>
      <linearGradient id="ls-frame-left-tab-edge" x1="6.634" y1="545" x2="6.634" y2="626" gradientUnits="userSpaceOnUse">
        <stop stop-color="#559FF8" stop-opacity="0.46" />
        <stop offset="1" stop-color="#4A88F8" stop-opacity="0.48" />
      </linearGradient>
      <linearGradient id="ls-frame-panel-fill" x1="359.006" y1="67.269" x2="43.215" y2="438.405" gradientUnits="userSpaceOnUse">
        <stop stop-color="#D9D9D9" />
        <stop offset="0.628" stop-color="#D9D9D9" stop-opacity="0" />
      </linearGradient>
      <filter id="ls-frame-shell-glow" x="-3%" y="-3%" width="106%" height="106%">
        <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#0DE0FF" flood-opacity="0.54" />
      </filter>
      <filter id="ls-frame-tab-glow" x="-80%" y="-8%" width="260%" height="116%">
        <feDropShadow dx="0" dy="0" stdDeviation="2" flood-color="#52C2FA" flood-opacity="0.72" />
      </filter>
    </defs>

    <g :transform="shellScale">
      <!-- 内框多层渐变底 -->
      <path
        d="M392.833 3H24.8035L3.31705 25.6716V532.637L14.6898 543.5V628L2.84319 640.5V828.5L3.79091 829.403L19.2752 845.75L19.9023 845L4.73864 829.112V828.681L5.2125 641.5L16.1114 630L16.5852 542.5L5.68637 532.637V27.3284L26.3738 5.5H395.202L392.833 3Z"
        fill="#788ABD"
        fill-opacity="var(--sidebar-frame-band-opacity)"
      />
      <path
        d="M392.833 3H24.8035L3.31705 25.6716V532.637L14.6898 543.5V628L2.84319 640.5V828.5L3.79091 829.403L19.2752 845.75L19.9023 845L4.73864 829.112V828.681L5.2125 641.5L16.1114 630L16.5852 542.5L5.68637 532.637V27.3284L26.3738 5.5H395.202L392.833 3Z"
        fill="url(#ls-frame-grad-top)"
      />
      <path
        d="M392.833 3H24.8035L3.31705 25.6716V532.637L14.6898 543.5V628L2.84319 640.5V828.5L3.79091 829.403L19.2752 845.75L19.9023 845L4.73864 829.112V828.681L5.2125 641.5L16.1114 630L16.5852 542.5L5.68637 532.637V27.3284L26.3738 5.5H395.202L392.833 3Z"
        fill="url(#ls-frame-grad-mid)"
      />
      <path
        d="M392.833 3H24.8035L3.31705 25.6716V532.637L14.6898 543.5V628L2.84319 640.5V828.5L3.79091 829.403L19.2752 845.75L19.9023 845L4.73864 829.112V828.681L5.2125 641.5L16.1114 630L16.5852 542.5L5.68637 532.637V27.3284L26.3738 5.5H395.202L392.833 3Z"
        fill="url(#ls-frame-grad-bottom)"
      />

      <!-- 主面板缺口轮廓 -->
      <path
        d="M416.052 26V246L404.68 257V406L416.052 418V829L395.202 851H25.5882L4.73863 829V641L16.1117 629V543L4.73863 532V26L25.5882 4H395.202L416.052 26Z"
        fill="#17234B"
        fill-opacity="var(--sidebar-panel-opacity)"
      />

      <!-- 内屏底色 -->
      <rect
        :x="innerScreen.x"
        :y="innerScreen.y"
        :width="innerScreen.width"
        :height="innerScreen.height"
        :rx="innerScreen.rx"
        fill="var(--sidebar-inner-fill)"
        stroke="#5F669F"
        stroke-opacity="0.06"
      />
      <rect
        :x="innerScreen.x"
        :y="innerScreen.y"
        :width="innerScreen.width"
        :height="innerScreen.height"
        :rx="innerScreen.rx"
        fill="url(#ls-frame-panel-fill)"
        fill-opacity="var(--sidebar-inner-highlight-opacity)"
      />

      <!-- 内描边加强 -->
      <path
        d="M416.052 26V246L404.68 257V406L416.052 418V829L395.202 851H25.5882L4.73863 829V641L16.1117 629V543L4.73863 532V26L25.5882 4H395.202L416.052 26Z"
        fill="none"
        stroke="#5F669F"
        stroke-opacity="0.43"
        stroke-width="2"
      />

      <!-- 右侧竖向高光 -->
      <path
        d="M394.728 852L415.578 830V419L404.206 407V258L415.578 247V27L394.728 5H325.07"
        stroke="url(#ls-frame-edge-glow)"
        stroke-width="2"
      />

      <!-- 右侧独立机械导轨 -->
      <g filter="url(#ls-frame-tab-glow)">
        <path
          d="M408.47 259L416.052 251V413L408.47 405V259Z"
          fill="url(#ls-frame-right-tab)"
          stroke="#547CE0"
          stroke-opacity="0.68"
          stroke-width="0.9"
        />
        <path
          d="M411.3 260.5L414.157 257.5V406.5L411.3 403.5V260.5Z"
          fill="#3155B7"
          fill-opacity="0.72"
        />
        <path
          d="M414.157 253L416.052 251V413L414.157 411V253Z"
          fill="url(#ls-frame-right-tab-edge)"
        />
        <path d="M415.15 255V409" stroke="#A6E7FF" stroke-opacity="0.76" stroke-width="0.8" />
      </g>

      <!-- 左侧短机械导轨 -->
      <g filter="url(#ls-frame-tab-glow)">
        <path
          d="M0.948 538L6.634 545V626L0.948 633V538Z"
          fill="url(#ls-frame-left-tab)"
          stroke="#4F7FEA"
          stroke-opacity="0.56"
          stroke-width="0.8"
        />
        <rect x="5.686" y="545" width="1.895" height="81" fill="url(#ls-frame-left-tab-edge)" />
      </g>

      <!-- 外轮廓青色发光 -->
      <g filter="url(#ls-frame-shell-glow)">
        <path
          d="M416.052 26V246L404.68 257V406L416.052 418V829L395.202 851H25.5882L4.73863 829V641L16.1117 629V543L4.73863 532V26L25.5882 4H395.202L416.052 26Z"
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
.left-sidebar-frame {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
