<script setup lang="ts">

import { computed } from 'vue'

import {

  CONTENT_MASK_GRADIENT,

  DASHBOARD_MASK_GRADIENT,

  DASHBOARD_MASK_VERTICAL,

  MOBILE_MASK_GRADIENT,

  type MapMaskVariant,

} from '../../constants/mapLayout'



const props = withDefaults(

  defineProps<{

    variant?: MapMaskVariant

  }>(),

  {

    variant: 'dashboard',

  },

)



const maskStyle = computed(() => {

  const mobileMask = MOBILE_MASK_GRADIENT

  if (props.variant === 'content') {

    return {

      background: CONTENT_MASK_GRADIENT,

      '--mobile-mask': mobileMask,

    }

  }

  return {

    background: `${DASHBOARD_MASK_GRADIENT}, ${DASHBOARD_MASK_VERTICAL}`,

    '--mobile-mask': mobileMask,

  }

})

</script>



<template>

  <div class="app-map-gradient-mask" :style="maskStyle" aria-hidden="true" />

</template>



<style scoped>

.app-map-gradient-mask {

  position: fixed;

  inset: 0;

  z-index: 1;

  pointer-events: none;

}



@media (max-width: 1320px) {

  .app-map-gradient-mask {

    background: var(--mobile-mask) !important;

  }

}

</style>


