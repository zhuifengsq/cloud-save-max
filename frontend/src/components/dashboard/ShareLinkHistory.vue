<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { clearDramaShareurlLogs } from '@/api/dashboard'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { TaskItem } from '@/types/tasks'
import { formatDateTime } from '@/utils/capacity'
import FailureReason from '@/components/dashboard/FailureReason.vue'

const props = defineProps<{ tasks: TaskItem[] }>()
const emit = defineEmits<{ cleared: [] }>()
const auth = useAuthStore()
const clearing = ref(false)
const expanded = ref(false)
const records = computed(() => props.tasks.flatMap((task) =>
  (Array.isArray(task.extra?.shareurl_update_logs) ? task.extra.shareurl_update_logs : [])
    .map((entry) => ({ ...entry, taskId: task.id, taskname: task.taskname })),
).sort((a, b) => b.at.localeCompare(a.at)))
const visibleRecords = computed(() => expanded.value ? records.value : records.value.slice(0, 6))

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
  <section class="share-history" aria-label="换链日志">
    <div class="share-history__header">
      <h3>换链日志 <span>{{ records.length }} 条</span></h3>
      <div class="share-history__actions">
        <el-button v-if="records.length > 6" text size="small" @click="expanded = !expanded">
          {{ expanded ? '收起' : '展开全部' }}
        </el-button>
        <el-button v-if="auth.permissions.includes(TASK_WRITE)" text size="small" type="danger"
          :loading="clearing" :disabled="!records.length" @click="clearLogs">
          清除日志
        </el-button>
      </div>
    </div>
    <p v-if="!records.length" class="share-history__empty">暂无换链记录，手动或自动换源后会显示在这里。</p>
    <div v-else class="share-history__list">
      <article v-for="entry in visibleRecords" :key="`${entry.taskId}-${entry.id}`" class="share-history__entry">
        <div class="share-history__title">
          <strong>{{ entry.taskname }}</strong>
          <el-tag size="small" :type="entry.status === 'success' ? 'success' : 'warning'">
            {{ entry.status === 'success' ? '换链成功' : '未换链' }}
          </el-tag>
        </div>
        <div class="share-history__meta">
          <time :datetime="entry.at">{{ formatDateTime(entry.at) }}</time>
          <span>{{ entry.source === 'auto' ? '自动换链' : '手动换链' }}</span>
          <span v-if="entry.round">第 {{ entry.round }}/4 轮</span>
        </div>
        <FailureReason v-if="entry.status === 'failed'" :message="entry.message" />
        <p v-else class="share-history__message">{{ entry.message }}</p>
        <details v-if="entry.old_shareurl || entry.new_shareurl" class="share-history__details">
          <summary>查看源链接</summary>
          <div v-if="entry.old_shareurl"><span>原链接</span><code>{{ entry.old_shareurl }}</code></div>
          <div v-if="entry.new_shareurl"><span>新链接</span><code>{{ entry.new_shareurl }}</code></div>
        </details>
      </article>
    </div>
    <p v-if="records.length" class="share-history__hint">每个任务保留最近 50 条换链记录；换链结果与转存结果分别记录。</p>
  </section>
</template>

<style scoped>
.share-history { margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--el-border-color-lighter); min-width: 0; }
.share-history__header, .share-history__title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.share-history__header { flex-wrap: wrap; }
.share-history__actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.share-history__actions .el-button + .el-button { margin-left: 0; }
.share-history h3 { margin: 0; font-size: 17px; }
.share-history h3 span { margin-left: 8px; color: var(--el-text-color-secondary); font-size: 12px; font-weight: 400; }
.share-history__list { display: grid; gap: 10px; margin-top: 14px; }
.share-history__entry { padding: 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 14px; background: var(--el-bg-color); min-width: 0; }
.share-history__title strong { font-size: 15px; overflow-wrap: anywhere; }
.share-history__title .el-tag { flex-shrink: 0; }
.share-history__meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.share-history__message { margin: 10px 0 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.share-history__details { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.share-history__details summary { cursor: pointer; width: fit-content; padding: 4px 0; }
.share-history__details div { display: grid; gap: 4px; margin-top: 8px; }
.share-history__details code { white-space: normal; overflow-wrap: anywhere; color: var(--el-text-color-regular); }
.share-history__empty, .share-history__hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.share-history__hint { margin: 12px 0 0; }
</style>
