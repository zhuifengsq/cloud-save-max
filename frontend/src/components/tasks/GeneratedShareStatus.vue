<script setup lang="ts">
import { computed } from 'vue'

import type { TaskExecutionItem, TaskShareLink } from '@/types/tasks'

const props = defineProps<{
  link: TaskShareLink | null
  executions?: TaskExecutionItem[]
}>()

const check = computed(() => {
  if (!props.link) return null
  for (const execution of props.executions || []) {
    const result = execution.adapter_snapshot?.generated_share_check
    if (result && result.share_id === (props.link.share_id || '') && result.saved_at === (props.link.saved_at || '')) {
      return result
    }
  }
  return null
})

const labels = {
  valid: { text: '有效', type: 'success' },
  invalid: { text: '已失效', type: 'danger' },
  pending: { text: '审核中', type: 'info' },
  error: { text: '检测失败', type: 'warning' },
} as const

const badge = computed(() => check.value ? labels[check.value.status] : { text: '待检测', type: 'info' as const })
const tip = computed(() => {
  if (!check.value) return '将在下次执行任务时检测'
  return [check.value.message, new Date(check.value.checked_at).toLocaleString()].filter(Boolean).join(' · ')
})
</script>

<template>
  <el-tooltip v-if="link" :content="tip" placement="top">
    <el-tag :type="badge.type" size="small" effect="plain">{{ badge.text }}</el-tag>
  </el-tooltip>
</template>
