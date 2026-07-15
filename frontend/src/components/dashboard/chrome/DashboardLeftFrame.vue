<script setup lang="ts">

import frameLeftBg from '../../../assets/design/chrome/frame-left.svg?url'

import { CHROME_SIDE_FRAME_WIDTH } from '../../../constants/dashboardChromeLayout'

</script>



<template>

  <div

    class="dashboard-frame dashboard-frame--left"

    :style="{ width: `${CHROME_SIDE_FRAME_WIDTH}px`, backgroundImage: `url(${frameLeftBg})` }"

    aria-hidden="true"

  />

</template>



<style scoped>

.dashboard-frame {

  position: fixed;

  top: 0;

  bottom: 0;

  z-index: 4;

  pointer-events: none;

  background-repeat: no-repeat;

  background-position: left top;

  background-size: 100% 100%;

}



.dashboard-frame--left {
  left: calc(-1 * var(--dashboard-frame-edge-outset, 0px));
}

</style>

