<script setup lang="ts">
import { computed } from 'vue'

import { describeCrontab, getNextExecutions } from '@/utils/cron'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    crontab: string
    timezone?: string
    title?: string
    width?: string
  }>(),
  {
    timezone: '',
    title: '执行计划预览',
    width: '480px',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const description = computed(() => describeCrontab(props.crontab) || '无法解析')
const nextRuns = computed(() => getNextExecutions(props.crontab, 5))
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    :width="width"
    :close-on-click-modal="true"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="cron-preview">
      <div>
        <div class="cron-preview__label">执行规则</div>
        <div class="cron-preview__value">{{ description }}</div>
      </div>
      <div>
        <div class="cron-preview__label">cron 表达式</div>
        <div class="cron-preview__code">
          <code>{{ crontab }}</code>
          <span v-if="timezone" class="cron-preview__tz">（{{ timezone }}）</span>
        </div>
      </div>
      <div>
        <div class="cron-preview__label">接下来执行时间</div>
        <ol v-if="nextRuns.length > 0" class="cron-preview__list">
          <li v-for="(time, idx) in nextRuns" :key="idx">
            {{ time.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' }) }}
          </li>
        </ol>
        <div v-else class="cron-preview__empty">无法计算</div>
      </div>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.cron-preview__tz {
  margin-left: 6px;
  font-family: inherit;
  color: var(--el-text-color-secondary);
}
</style>
