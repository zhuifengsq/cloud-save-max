<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { clearDramaShareurlLogs } from '@/api/dashboard'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { DramaShareurlUpdateLog } from '@/types/dashboard'
import { formatDateTime } from '@/utils/capacity'
import DashboardLogList from '@/components/dashboard/DashboardLogList.vue'
import FailureReason from '@/components/dashboard/FailureReason.vue'

const props = defineProps<{ records: DramaShareurlUpdateLog[] }>()
const emit = defineEmits<{ cleared: [] }>()
const auth = useAuthStore()
const clearing = ref(false)
const expanded = ref(false)
const records = computed(() => props.records.slice().sort((a, b) => b.at.localeCompare(a.at)))

async function clearLogs() {
  if (clearing.value || !records.value.length) return
  clearing.value = true
  try {
    const result = await clearDramaShareurlLogs()
    expanded.value = false
    emit('cleared')
    ElMessage.success(`已清除 ${result.deleted} 条换链日志`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.message || error?.message || '清除换链日志失败')
  } finally {
    clearing.value = false
  }
}
</script>

<template>
  <DashboardLogList
    v-model:expanded="expanded"
    title="换链日志"
    :records="records"
    :record-key="entry => `${entry.task_id}-${entry.id}`"
    empty-text="暂无换链记录，手动或自动换源后会显示在这里。"
  >
    <template #actions>
      <el-button v-if="auth.permissions.includes(TASK_WRITE)" text size="small" type="danger"
        :loading="clearing" :disabled="!records.length" @click="clearLogs">
        清除日志
      </el-button>
    </template>
    <template #status="{ record }">
      <el-tag size="small" :type="record.status === 'success' ? 'success' : 'warning'">
        {{ record.status === 'success' ? '换链成功' : '未换链' }}
      </el-tag>
    </template>
    <template #meta="{ record }">
      <time :datetime="record.at">{{ formatDateTime(record.at) }}</time>
      <span>{{ record.source === 'auto' ? '自动换链' : '手动换链' }}</span>
      <span v-if="record.round">第 {{ record.round }}/4 轮</span>
    </template>
    <template #default="{ record }">
      <FailureReason v-if="record.status === 'failed'" :message="record.message" />
      <p v-else class="share-history__message">{{ record.message }}</p>
      <details v-if="record.old_shareurl || record.new_shareurl" class="share-history__details">
        <summary>查看源链接</summary>
        <div v-if="record.old_shareurl"><span>原链接</span><code>{{ record.old_shareurl }}</code></div>
        <div v-if="record.new_shareurl"><span>新链接</span><code>{{ record.new_shareurl }}</code></div>
      </details>
    </template>
    <template #footer>
      <p v-if="records.length" class="share-history__hint">每个任务保留最近 50 条换链记录；换链结果与转存结果分别记录。</p>
    </template>
  </DashboardLogList>
</template>

<style scoped>
.share-history__message { margin: 10px 0 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.share-history__details { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.share-history__details summary { cursor: pointer; width: fit-content; padding: 4px 0; }
.share-history__details div { display: grid; gap: 4px; margin-top: 8px; }
.share-history__details code { white-space: normal; overflow-wrap: anywhere; color: var(--el-text-color-regular); }
.share-history__hint { margin: 12px 0 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
</style>
