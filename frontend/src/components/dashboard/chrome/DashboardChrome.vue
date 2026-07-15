<script setup lang="ts">
import DashboardBottomCenter from './DashboardBottomCenter.vue'
import DashboardBottomIcons from './DashboardBottomIcons.vue'
import DashboardBottomRailLeft from './DashboardBottomRailLeft.vue'
import DashboardBottomRailRight from './DashboardBottomRailRight.vue'
import DashboardHeader from './DashboardHeader.vue'
import DashboardLeftFrame from './DashboardLeftFrame.vue'
import DashboardRightFrame from './DashboardRightFrame.vue'
import DashboardTopBorderRail from './DashboardTopBorderRail.vue'
</script>

<template>
  <div class="dashboard-chrome" aria-hidden="false">
    <DashboardLeftFrame />
    <DashboardRightFrame />
    <DashboardTopBorderRail />
    <DashboardHeader />
    <DashboardBottomRailLeft />
    <DashboardBottomRailRight />
    <DashboardBottomCenter />
    <DashboardBottomIcons />
  </div>
</template>

<style scoped>
.dashboard-chrome {
  position: fixed;
  inset: 0;
  z-index: 4;
  pointer-events: none;
}
</style>
