<script setup lang="ts">
/** A headline number with a caption: the top row of the overview screens. */
defineProps<{ label: string; value: string | number; unit?: string; sub?: string; tone?: 'ok' | 'warning' | 'critical' }>()
</script>

<template>
  <div class="kpi" :class="tone && `t-${tone}`">
    <div class="label">{{ label }}</div>
    <div class="value mono">{{ value }}<small v-if="unit"> {{ unit }}</small></div>
    <div v-if="sub" class="sub">{{ sub }}</div>
    <slot />
  </div>
</template>

<style scoped>
.kpi {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  display: grid;
  gap: 2px;
  min-width: 0;
}
.label,
.sub {
  color: var(--text-muted);
  font-size: 12px;
}
.value {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
}
.value small {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: 400;
}
.t-ok .value {
  color: var(--st-ok);
}
.t-warning .value {
  color: var(--st-warning);
}
.t-critical .value {
  color: var(--st-critical);
}
</style>
