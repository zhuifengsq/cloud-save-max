<script setup lang="ts">
import { computed } from 'vue'
import { summarizeFailure } from '@/utils/failureMessage'

const props = defineProps<{ message?: string | null }>()
const reason = computed(() => summarizeFailure(props.message))
</script>

<template>
  <div class="failure-reason">
    <p class="failure-reason__summary">{{ reason.summary }}</p>
    <details v-if="reason.detail" class="failure-reason__details">
      <summary>查看详细错误</summary>
      <pre>{{ reason.detail }}</pre>
    </details>
  </div>
</template>

<style scoped>
.failure-reason { min-width: 0; margin-top: 8px; }
.failure-reason__summary { margin: 0; font-size: 13px; line-height: 1.6; color: var(--el-text-color-regular); overflow-wrap: anywhere; }
.failure-reason__details { margin-top: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.failure-reason__details summary { width: fit-content; cursor: pointer; padding: 3px 0; }
.failure-reason__details pre { max-height: 180px; overflow: auto; margin: 8px 0 0; padding: 10px 12px; background: var(--el-fill-color-light); border-radius: 8px; font-size: 12px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
