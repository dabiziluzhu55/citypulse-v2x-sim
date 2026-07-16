<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string
    unit?: string
    accent?: 'cyan' | 'success' | 'warning' | 'danger'
  }>(),
  {
    unit: '',
    accent: 'cyan',
  },
)
</script>

<template>
  <article class="overview-stat-card" :class="`overview-stat-card--${accent}`">
    <span class="overview-stat-card__label">{{ label }}</span>
    <div class="overview-stat-card__reading">
      <strong>{{ value }}</strong>
      <span v-if="unit">{{ unit }}</span>
    </div>
  </article>
</template>

<style scoped>
.overview-stat-card {
  min-width: 0;
  padding: 16px;
  border: 1px solid rgba(0, 210, 255, 0.2);
  border-radius: 10px;
  background: rgba(3, 18, 36, 0.72);
}

.overview-stat-card--success { border-color: rgba(55, 214, 122, 0.35); }
.overview-stat-card--warning { border-color: rgba(255, 191, 71, 0.4); }
.overview-stat-card--danger { border-color: rgba(255, 95, 122, 0.4); }

.overview-stat-card__label {
  display: block;
  margin-bottom: 9px;
  color: var(--cp-text-muted);
  font-size: 13px;
}

.overview-stat-card__reading {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--cp-text-primary);
}

.overview-stat-card__reading strong {
  overflow: hidden;
  font-size: 24px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-stat-card__reading span {
  color: var(--cp-text-secondary);
  font-size: 12px;
}
</style>
