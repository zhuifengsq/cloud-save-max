<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import { listDramaLeadInOwnDir, readLeadInErrorMessage } from '@/api/dramaLeadIn'
import { useIsMobile } from '@/composables/useIsMobile'
import type { DramaLeadInOwnDirEntry } from '@/types/dramaLeadIn'
import { formatBytes } from '@/utils/capacity'

// 本账号文件目录浏览弹窗：只读展示账号内目录，选中单个文件后把完整路径交给父组件。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    driveType: string
    driveName?: string
    accountName: string
    startPath?: string
  }>(),
  { driveName: '', startPath: '/' },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'picked', path: string): void
}>()

const isMobile = useIsMobile()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const title = computed(() => {
  const driveName = props.driveName || props.driveType || '网盘'
  const accountName = props.accountName || '未选择账号'
  return `选择素材文件 · ${driveName} / ${accountName}`
})
const dialogWidth = computed(() => (isMobile.value ? '94%' : '720px'))

const state = reactive({
  loading: false,
  error: '',
  dirPath: '/',
  entries: [] as DramaLeadInOwnDirEntry[],
  selectedPath: '',
  selectedName: '',
})

// 面包屑从根目录开始，点击任意层级可跳转。
const breadcrumbs = computed(() => {
  const items = [{ label: '根目录', path: '/' }]
  let current = ''
  for (const segment of state.dirPath.split('/').filter(Boolean)) {
    current += `/${segment}`
    items.push({ label: segment, path: current })
  }
  return items
})

const parentPath = computed(() => {
  const segments = state.dirPath.split('/').filter(Boolean)
  if (!segments.length) return '/'
  return `/${segments.slice(0, -1).join('/')}`
})
const currentPathText = computed(() => state.dirPath || '/')
const canConfirm = computed(() => Boolean(state.selectedPath) && !state.loading)

function normalizePath(value?: string | null) {
  const raw = String(value || '').trim()
  if (!raw) return '/'
  const normalized = raw.replace(/\\/g, '/').replace(/\/+/g, '/')
  return normalized.startsWith('/') ? normalized : `/${normalized}`
}

// 目录列表不缓存：每次进入（含账号切换）都重新请求，避免展示过期内容。
async function loadDir(path?: string) {
  if (!props.accountName) {
    state.error = '请先选择素材账号'
    state.entries = []
    return
  }
  const target = normalizePath(path || state.dirPath)
  state.loading = true
  state.error = ''
  state.selectedPath = ''
  state.selectedName = ''
  try {
    const result = await listDramaLeadInOwnDir({
      drive_type: props.driveType,
      account_name: props.accountName,
      dir_path: target,
    })
    state.dirPath = normalizePath(result?.path || target)
    state.entries = (result?.entries || []).map((entry) => ({
      fid: String(entry.fid || ''),
      name: String(entry.name || ''),
      dir: Boolean(entry.dir),
      size: typeof entry.size === 'number' ? entry.size : null,
      path: normalizePath(entry.path || ''),
    }))
  } catch (error) {
    // 浏览失败时停留在当前层级并清空列表，避免误选上一级的残留条目。
    state.entries = []
    state.error = readLeadInErrorMessage(error, '读取目录失败，请稍后重试')
  } finally {
    state.loading = false
  }
}

function resetState() {
  state.loading = false
  state.error = ''
  state.dirPath = '/'
  state.entries = []
  state.selectedPath = ''
  state.selectedName = ''
}

function handleRowClick(entry: DramaLeadInOwnDirEntry) {
  if (entry.dir) {
    loadDir(entry.path)
    return
  }
  state.selectedPath = entry.path
  state.selectedName = entry.name
}

function handleRowDblClick(entry: DramaLeadInOwnDirEntry) {
  if (entry.dir) loadDir(entry.path)
}

function rowClassName({ row }: { row: DramaLeadInOwnDirEntry }) {
  return row.path === state.selectedPath ? 'is-selected' : ''
}

function entrySizeText(entry: DramaLeadInOwnDirEntry) {
  return entry.dir ? '文件夹' : formatBytes(entry.size)
}

function close() {
  if (state.loading) return
  visible.value = false
}

function confirmPick() {
  if (!canConfirm.value) return
  emit('picked', state.selectedPath)
  visible.value = false
}

watch(
  () => props.modelValue,
  (value) => {
    if (!value) return
    resetState()
    loadDir(props.startPath || '/')
  },
)

watch(
  () => [props.driveType, props.accountName],
  () => {
    if (!props.modelValue) return
    resetState()
    loadDir('/')
  },
)
</script>

<template>
  <el-dialog v-model="visible" :title="title" :width="dialogWidth" append-to-body destroy-on-close>
    <div class="dir-picker">
      <el-breadcrumb separator="/" class="dir-picker__breadcrumb">
        <el-breadcrumb-item v-for="(item, index) in breadcrumbs" :key="item.path">
          <a v-if="index !== breadcrumbs.length - 1" href="#" @click.prevent="loadDir(item.path)">{{ item.label }}</a>
          <span v-else class="dir-picker__current">{{ item.label }}</span>
        </el-breadcrumb-item>
      </el-breadcrumb>

      <div class="dir-picker__toolbar">
        <el-button size="small" :disabled="state.dirPath === '/' || state.loading" @click="loadDir(parentPath)">返回上级</el-button>
        <el-button size="small" :loading="state.loading" @click="loadDir()">刷新</el-button>
        <span class="dir-picker__hint">点击文件夹进入下一级，点击文件选中；只能选择单个文件。</span>
      </div>

      <el-alert
        v-if="state.error"
        type="error"
        :closable="false"
        show-icon
        :title="state.error"
        class="dir-picker__alert"
      />

      <el-table
        :data="state.entries"
        v-loading="state.loading"
        size="small"
        style="width: 100%"
        :row-class-name="rowClassName"
        @row-click="handleRowClick"
        @row-dblclick="handleRowDblClick"
      >
        <el-table-column label="名称" min-width="360">
          <template #default="{ row }">
            <span class="dir-picker__entry">
              <el-tag :type="row.dir ? 'info' : 'success'" size="small" effect="plain">{{ row.dir ? '文件夹' : '文件' }}</el-tag>
              <span class="dir-picker__entry-name">{{ row.name }}</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="120">
          <template #default="{ row }">
            <span class="dir-picker__entry-size">{{ entrySizeText(row) }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :image-size="60" :description="state.loading ? '加载中…' : '当前目录为空'" />
        </template>
      </el-table>

      <div class="dir-picker__footer">
        <span class="dir-picker__path">当前路径：{{ currentPathText }}</span>
        <span class="dir-picker__selected">
          {{ state.selectedPath ? `已选择：${state.selectedName}` : '请选择一个文件' }}
        </span>
      </div>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :disabled="!canConfirm" @click="confirmPick">确定</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dir-picker {
  min-height: 160px;
}
.dir-picker__breadcrumb {
  margin-bottom: 10px;
}
.dir-picker__current {
  color: var(--el-text-color-regular);
}
.dir-picker__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.dir-picker__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.dir-picker__alert {
  margin-bottom: 10px;
}
.dir-picker__entry {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.dir-picker__entry-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-picker__entry-size {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.dir-picker__footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.dir-picker__path {
  word-break: break-all;
}
.dir-picker__selected {
  color: var(--el-text-color-regular);
}
.dir-picker :deep(.el-table__body tr.is-selected > td) {
  background: var(--el-color-primary-light-9);
}
.dir-picker :deep(.el-table__row) {
  cursor: pointer;
}
</style>
