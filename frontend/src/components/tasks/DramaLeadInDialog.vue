<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { confirmTaskLeadIn, previewTaskLeadIn, readLeadInErrorCode, readLeadInErrorMessage } from '@/api/dramaLeadIn'
import { useIsMobile } from '@/composables/useIsMobile'
import type { DramaLeadInConfirmResult, DramaLeadInPreview, TaskLeadInSummary } from '@/types/dramaLeadIn'
import type { TaskItem } from '@/types/tasks'
import { formatDateTimeText } from '@/utils/datetime'

const props = defineProps<{
  modelValue: boolean
  task: TaskItem | null
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'updated', payload: { taskId: number; lead_in: TaskLeadInSummary }): void
}>()

const isMobile = useIsMobile()

const STATE_META: Record<string, { label: string; tagType: 'success' | 'info' | 'warning' | 'danger' }> = {
  absent: { label: '未添加', tagType: 'info' },
  none: { label: '未添加', tagType: 'info' },
  added: { label: '已添加', tagType: 'success' },
  exists: { label: '已存在', tagType: 'success' },
  conflict: { label: '同名冲突', tagType: 'danger' },
  pending: { label: '待确认', tagType: 'warning' },
  failed: { label: '添加失败', tagType: 'danger' },
}

const state = reactive({
  loading: false,
  confirming: false,
  preview: null as DramaLeadInPreview | null,
  result: null as DramaLeadInConfirmResult | null,
  error: '',
})

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const title = computed(() => (props.task ? `引流文件 - ${props.task.taskname}` : '引流文件'))
const dialogWidth = computed(() => (isMobile.value ? '94%' : '600px'))
const previewStateMeta = computed(() => STATE_META[String(state.preview?.state || 'absent')] || STATE_META.absent)
const canConfirm = computed(
  () => !state.loading && !state.confirming && Boolean(state.preview?.available) && Boolean(state.preview?.preview_token),
)

const previewDrive = computed(() => state.preview?.drive?.drive_name || '—')
const previewAccount = computed(() => {
  const account = state.preview?.account
  if (!account) return '—'
  return account.nickname ? `${account.account_name}（${account.nickname}）` : account.account_name
})
const previewDirectory = computed(() => state.preview?.directory?.path || '—')
const previewMaterial = computed(() => state.preview?.material?.name || '—')
const previewMaterialSource = computed(() => {
  const material = state.preview?.material
  if (!material) return ''
  return material.source === 'own_file' ? '本账号文件' : '分享文件'
})
const previewMaterialExtra = computed(() => {
  const material = state.preview?.material
  if (!material) return ''
  return [material.account_name, material.path].filter(Boolean).join(' · ')
})

const resultAlert = computed(() => {
  if (!state.result) return null
  const status = String(state.result.status || '')
  const type = status === 'added' || status === 'exists' ? 'success' : status === 'pending' ? 'warning' : 'error'
  const titleMap: Record<string, string> = {
    added: '引流文件已添加',
    exists: '目标目录已存在该文件',
    conflict: '存在同名冲突（未覆盖）',
    pending: '添加结果待确认',
    failed: '添加失败',
  }
  const detail = [state.result.material?.name, state.result.directory?.path].filter(Boolean).join(' · ')
  return {
    type: type as 'success' | 'warning' | 'error',
    title: titleMap[status] || '处理结果',
    description: [state.result.message, detail].filter(Boolean).join('；'),
  }
})

async function loadPreview() {
  if (!props.task) return
  state.loading = true
  state.error = ''
  state.result = null
  state.preview = null
  try {
    state.preview = await previewTaskLeadIn(props.task.id)
    syncLeadInSummary()
  } catch (error) {
    state.error = readLeadInErrorMessage(error, '读取引流状态失败')
  } finally {
    state.loading = false
  }
}

// 预览时网盘查重会重新确认目标目录里有没有该文件。若查重结果显示未添加，
// 说明文件已被删除（或其他原因不在目录里），把列表「引流」列同步成「—」，
// 后端预览同时会清掉过期记录，刷新后仍保持未添加。
function syncLeadInSummary() {
  const task = props.task
  const preview = state.preview
  if (!preview?.available || !task) return
  const previewState = String(preview.state || '')
  if (previewState !== 'absent' && previewState !== 'none') return
  const current = String(task.lead_in?.state || '')
  if (!current || current === 'none' || current === 'absent') return
  emit('updated', { taskId: task.id, lead_in: { state: 'none', last_added_at: null } })
}

async function reloadPreviewAfterConfirm() {
  const latest = state.result
  await loadPreview()
  state.result = latest
}

function close() {
  if (state.confirming) return
  visible.value = false
}

async function handleConfirm() {
  if (!props.task || !state.preview?.preview_token || state.confirming) return
  state.confirming = true
  try {
    const result = await confirmTaskLeadIn(props.task.id, { preview_token: state.preview.preview_token })
    state.result = result
    const status = String(result.status || '')
    if (status === 'added' || status === 'exists') {
      ElMessage.success(result.message || (status === 'added' ? '引流文件已添加' : '目标目录已存在该文件'))
    } else if (status === 'conflict') {
      ElMessage.warning(result.message || '存在同名冲突，未覆盖')
    } else if (status === 'pending') {
      ElMessage.warning(result.message || '添加结果待确认，请稍后刷新查看')
    } else {
      ElMessage.error(result.message || '添加失败')
    }
    if (status) {
      emit('updated', {
        taskId: props.task.id,
        lead_in: { state: status, last_added_at: result.added_at || null },
      })
    }
    await reloadPreviewAfterConfirm()
  } catch (error) {
    const code = readLeadInErrorCode(error)
    if (code === 'LEAD_IN_STALE_PREVIEW') {
      ElMessage.warning('预览已过期，已重新读取，请确认后再次添加')
      await loadPreview()
    } else if (code === 'LEAD_IN_IN_PROGRESS') {
      ElMessage.warning('该任务正在处理引流文件，请稍后再试')
    } else {
      ElMessage.error(readLeadInErrorMessage(error, '添加引流文件失败'))
    }
  } finally {
    state.confirming = false
  }
}

watch(
  () => props.modelValue,
  (value) => {
    if (value) loadPreview()
  },
)

watch(
  () => props.task?.id,
  () => {
    if (props.modelValue) loadPreview()
  },
)
</script>

<template>
  <el-dialog v-model="visible" :title="title" :width="dialogWidth" append-to-body destroy-on-close @closed="state.result = null">
    <div v-loading="state.loading" class="lead-in-dialog">
      <el-alert v-if="state.error" type="error" :closable="false" show-icon :title="state.error" />

      <template v-if="state.preview">
        <el-alert
          v-if="!state.preview.available"
          type="warning"
          :closable="false"
          show-icon
          :title="state.preview.reason || '当前任务暂不可添加引流文件'"
        />

        <el-descriptions v-else :column="1" border size="small" class="lead-in-dialog__desc">
          <el-descriptions-item label="网盘">{{ previewDrive }}</el-descriptions-item>
          <el-descriptions-item label="目标账号">{{ previewAccount }}</el-descriptions-item>
          <el-descriptions-item label="保存目录">{{ previewDirectory }}</el-descriptions-item>
          <el-descriptions-item label="素材文件">{{ previewMaterial }}</el-descriptions-item>
          <el-descriptions-item v-if="previewMaterialSource" label="素材来源">
            <el-tag :type="state.preview?.material?.source === 'own_file' ? 'warning' : 'info'" size="small" effect="plain">
              {{ previewMaterialSource }}
            </el-tag>
            <span v-if="previewMaterialExtra" class="lead-in-dialog__hint">{{ previewMaterialExtra }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="当前状态">
            <el-tag :type="previewStateMeta.tagType" size="small" effect="plain">{{ previewStateMeta.label }}</el-tag>
            <span v-if="state.preview.state_message" class="lead-in-dialog__hint">{{ state.preview.state_message }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="最近添加">{{ formatDateTimeText(state.preview.last_added_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="state.preview.expires_at" label="预览有效期">
            {{ formatDateTimeText(state.preview.expires_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="state.preview.available"
          type="info"
          :closable="false"
          show-icon
          class="lead-in-dialog__tip"
          title="确认添加后会在上述目录生成引流文件；同名文件不会被覆盖。"
        />
      </template>

      <el-alert
        v-if="resultAlert"
        :type="resultAlert.type"
        :closable="false"
        show-icon
        class="lead-in-dialog__result"
        :title="resultAlert.title"
        :description="resultAlert.description"
      />
    </div>

    <template #footer>
      <el-button :disabled="state.confirming" @click="close">关闭</el-button>
      <el-button
        v-if="!state.result && (state.error || (state.preview && !state.preview.available))"
        :loading="state.loading"
        @click="loadPreview"
      >重新预览</el-button>
      <el-button v-else-if="!state.result" type="primary" :loading="state.confirming" :disabled="!canConfirm" @click="handleConfirm">
        确认添加
      </el-button>
      <el-button v-else :loading="state.loading" @click="loadPreview">重新预览</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.lead-in-dialog {
  min-height: 120px;
}
.lead-in-dialog__desc {
  margin-top: 12px;
}
.lead-in-dialog__hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.lead-in-dialog__tip {
  margin-top: 12px;
}
.lead-in-dialog__result {
  margin-top: 12px;
}
</style>
