<script setup lang="ts" generic="T extends { taskname: string }">
import { computed, useId } from 'vue'

const props = defineProps<{
  title: string
  records: T[]
  recordKey: (record: T) => string
  emptyText?: string
}>()

const expanded = defineModel<boolean>('expanded', { required: true })
const listId = useId()
const previewCount = 3
const visibleRecords = computed(() => expanded.value ? props.records : props.records.slice(0, previewCount))
</script>

<template>
  <article class="glass-panel dashboard-log" :aria-label="title">
    <header class="dashboard-log__header">
      <h2>{{ title }} <span>{{ records.length }} 条</span></h2>
      <div class="dashboard-log__actions">
        <el-button
          v-if="records.length > previewCount"
          text
          size="small"
          :aria-expanded="expanded"
          :aria-controls="listId"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起' : '展开全部' }}
        </el-button>
        <slot name="actions" />
      </div>
    </header>
    <p v-if="!records.length && emptyText" class="dashboard-log__empty">{{ emptyText }}</p>
    <ul v-if="records.length" :id="listId" class="dashboard-log__list" role="list">
      <li v-for="record in visibleRecords" :key="recordKey(record)" class="dashboard-log__entry">
        <div class="dashboard-log__title">
          <strong>{{ record.taskname }}</strong>
          <slot name="status" :record="record" />
        </div>
        <div class="dashboard-log__meta">
          <slot name="meta" :record="record" />
        </div>
        <slot :record="record" />
      </li>
    </ul>
    <slot name="footer" />
  </article>
</template>

<style scoped>
.dashboard-log { padding: 22px; min-inline-size: 0; }
.dashboard-log__header, .dashboard-log__title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.dashboard-log__header { flex-wrap: wrap; }
.dashboard-log__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 4px; margin-inline-start: auto; }
.dashboard-log__actions :deep(.el-button + .el-button) { margin-inline-start: 0; }
.dashboard-log h2 { margin: 0; font-size: 18px; }
.dashboard-log h2 span, .dashboard-log__meta { color: var(--el-text-color-secondary); font-size: 12px; }
.dashboard-log h2 span { margin-inline-start: 8px; font-weight: 400; white-space: nowrap; }
.dashboard-log__list { display: grid; gap: 10px; margin: 14px 0 0; padding: 0; list-style: none; }
.dashboard-log__entry { padding: 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 14px; background: var(--el-bg-color); min-width: 0; }
.dashboard-log__title strong { font-size: 15px; overflow-wrap: anywhere; }
.dashboard-log__title :slotted(.el-tag) { flex-shrink: 0; }
.dashboard-log__meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; }
.dashboard-log__empty { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
</style>
