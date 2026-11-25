<script setup lang="ts">
import { Coin, DataAnalysis, Odometer, Warning } from '@element-plus/icons-vue'
import type { CapacitySummary } from '@/types/dashboard'
import { formatBytes, formatPercent } from '@/utils/capacity'

const props = defineProps<{
  summary: CapacitySummary
}>()

const metrics = computed(() => [
  {
    label: '总容量',
    value: formatBytes(props.summary.total_space),
    hint: `${props.summary.capacity_account_count} 个账号可统计`,
    icon: Coin,
    tone: 'brand',
    warn: false,
  },
  {
    label: '已使用',
    value: formatBytes(props.summary.total_used_space),
    hint: '基于最近一次容量快照',
    icon: DataAnalysis,
    tone: 'cyan',
    warn: false,
  },
  {
    label: '整体占比',
    value: formatPercent(props.summary.usage_ratio),
    hint: props.summary.total_space ? '所有支持容量的网盘汇总' : '暂无容量数据',
    icon: Odometer,
    tone: 'violet',
    warn: false,
  },
  {
    label: '预警账号',
    value: String(props.summary.warning_account_count),
    hint: `${props.summary.account_count} 个账号中需要关注`,
    icon: Warning,
    tone: props.summary.warning_account_count > 0 ? 'warn' : 'success',
    warn: props.summary.warning_account_count > 0,
  },
])
</script>

<template>
  <section class="metric-strip">
    <div
      v-for="metric in metrics"
      :key="metric.label"
      class="glass-panel metric-tile"
      :class="{ 'metric-tile--warn': metric.warn }"
    >
      <div class="metric-tile__head">
        <div class="metric-tile__label">{{ metric.label }}</div>
        <span class="metric-tile__icon" :class="`metric-tile__icon--${metric.tone}`" aria-hidden="true">
          <el-icon><component :is="metric.icon" /></el-icon>
        </span>
      </div>
      <div class="metric-tile__value">{{ metric.value }}</div>
      <div class="metric-tile__hint">{{ metric.hint }}</div>
    </div>
  </section>
</template>
