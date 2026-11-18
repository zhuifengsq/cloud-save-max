<script lang="ts">
import type { TaskShareLink, TaskSummary } from '@/types/tasks'
import type { DriveType } from '@/utils/driveType'

export type DramaTaskGroupItem = {
  task: TaskSummary
  drive: DriveType | null
  account: string
  share: TaskShareLink | null
  leadIn: { label: string; type: 'success' | 'info' | 'warning' | 'danger' } | null
  leadInTime: string
  linkFailure: string | null
}

export type DramaTaskGroupData = {
  key: string
  title: string
  tmdbId: number | null
  mediaType: string | null
  items: DramaTaskGroupItem[]
  drives: DriveType[]
  failed: number
  invalidShares: number
  enabledCount: number
  ended: '完结' | '连载' | null
}
</script>

<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'

import DriveTypeIcon from './DriveTypeIcon.vue'
import GeneratedShareStatus from './GeneratedShareStatus.vue'

defineProps<{
  group: DramaTaskGroupData
  open: boolean
  openExecutions: ReadonlySet<number>
  canRun: boolean
  canWrite: boolean
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  'update:execution-open': [taskId: number, open: boolean]
  run: [task: TaskSummary]
  edit: [task: TaskSummary]
  command: [command: string, task: TaskSummary]
  copy: [task: TaskSummary]
  history: [taskId: number]
  execution: [taskId: number, executionId: number]
}>()

function isOpen(event: Event) {
  return (event.currentTarget as HTMLDetailsElement).open
}

function mediaLabel(type: string | null) {
  if (type === 'tv') return '剧集'
  if (type === 'movie') return '电影'
  return '条目'
}

const executionLabels: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '执行中',
  skipped: '已跳过',
}
</script>

<template>
  <details
    class="drama-group"
    :class="{ 'drama-group--failed': group.failed > 0 }"
    :open="open"
    @toggle="emit('update:open', isOpen($event))"
  >
    <summary class="drama-group__summary">
      <span class="drama-group__chevron" aria-hidden="true"><ArrowRight /></span>
      <span class="drama-group__heading">
        <strong>{{ group.title }}</strong>
        <span class="drama-group__meta">
          <span>{{ group.items.length }} 个任务</span>
          <span>{{ group.tmdbId ? `TMDB ${mediaLabel(group.mediaType)} #${group.tmdbId}` : '未关联 TMDB' }}</span>
        </span>
      </span>
      <span class="drama-group__status">
        <span v-if="group.drives.length" class="drama-group__drives">
          <DriveTypeIcon v-for="drive in group.drives" :key="drive" :type="drive" :size="20" />
        </span>
        <el-tag v-if="group.ended" :type="group.ended === '完结' ? 'success' : 'info'" size="small" effect="plain">
          {{ group.ended }}
        </el-tag>
        <span class="drama-group__count">启用 {{ group.enabledCount }}/{{ group.items.length }}</span>
        <el-tag v-if="group.invalidShares" type="danger" size="small" effect="light">
          {{ group.invalidShares }} 条分享链接失效
        </el-tag>
        <el-tag v-if="group.failed" type="danger" size="small" effect="light">{{ group.failed }} 个失败</el-tag>
      </span>
    </summary>

    <div v-if="open" class="drama-group__body">
      <article
        v-for="{ task, drive, account, share, leadIn, leadInTime, linkFailure } in group.items"
        :key="task.id"
        class="drama-group-task"
      >
        <header class="drama-group-task__header">
          <h3 class="drama-group-task__name">
            <DriveTypeIcon :type="drive" />
            <span>{{ task.taskname }}</span>
          </h3>
          <div class="drama-group-task__tags">
            <el-tag :type="task.enabled ? 'success' : 'info'" size="small" effect="plain">
              {{ task.enabled ? '启用' : '禁用' }}
            </el-tag>
            <el-tag
              v-if="task.tmdb_media_type === 'tv' && (task.tmdb_is_ended === true || task.tmdb_status)"
              :type="task.tmdb_is_ended ? 'success' : 'info'"
              size="small"
              effect="plain"
            >
              {{ task.tmdb_is_ended ? '完结' : '连载' }}
            </el-tag>
            <el-tag v-if="task.executions?.[0]?.status === 'failed' && !linkFailure" type="danger" size="small" effect="light">
              执行失败
            </el-tag>
          </div>
        </header>

        <p v-if="linkFailure" class="drama-group-task__warning">{{ linkFailure }}</p>

        <dl class="drama-group-task__fields">
          <div>
            <dt>账号</dt>
            <dd>{{ account }}</dd>
          </div>
          <div>
            <dt>保存路径</dt>
            <dd>{{ task.savepath || '—' }}</dd>
          </div>
          <div>
            <dt>分享链接</dt>
            <dd v-if="share" class="drama-group-task__inline">
              <el-tooltip :content="share.share_url" :trigger="['hover', 'focus']" placement="top">
                <a :href="share.share_url" target="_blank" rel="noopener noreferrer">{{ share.share_url }}</a>
              </el-tooltip>
              <el-button link type="primary" @click="emit('copy', task)">复制</el-button>
              <GeneratedShareStatus :link="share" :executions="task.executions" />
            </dd>
            <dd v-else class="drama-group-task__muted">未生成</dd>
          </div>
          <div>
            <dt>引流文件</dt>
            <dd v-if="leadIn" class="drama-group-task__inline">
              <el-tag :type="leadIn.type" size="small" effect="plain">{{ leadIn.label }}</el-tag>
              <span v-if="leadInTime" class="drama-group-task__muted">{{ leadInTime }}</span>
            </dd>
            <dd v-else class="drama-group-task__muted">—</dd>
          </div>
        </dl>

        <footer v-if="canRun || canWrite" class="drama-group-task__actions">
          <el-button v-if="canRun" size="small" type="primary" @click="emit('run', task)">执行</el-button>
          <el-button v-if="canWrite" size="small" @click="emit('edit', task)">编辑</el-button>
          <el-dropdown v-if="canWrite" trigger="click" @command="(command: string) => emit('command', command, task)">
            <el-button size="small">更多</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="toggle">{{ task.enabled ? '禁用' : '启用' }}</el-dropdown-item>
                <el-dropdown-item command="share">分享</el-dropdown-item>
                <el-dropdown-item command="lead_in">引流文件</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </footer>

        <details
          class="drama-group-task__history"
          :open="openExecutions.has(task.id)"
          @toggle="emit('update:execution-open', task.id, isOpen($event))"
        >
          <summary>
            <ArrowRight class="drama-group-task__history-arrow" aria-hidden="true" />
            <span>最近执行</span>
            <span v-if="task.executions?.[0]" class="drama-group-task__muted">
              {{ executionLabels[task.executions[0].status] || task.executions[0].status }}
            </span>
            <span v-else class="drama-group-task__muted">暂无记录</span>
          </summary>
          <div class="drama-group-task__history-body">
            <ul v-if="task.executions?.length">
              <li v-for="execution in task.executions.slice(0, 3)" :key="execution.id">
                <span class="drama-group-task__execution">
                  <strong>{{ executionLabels[execution.status] || execution.status }}</strong>
                  <span class="drama-group-task__muted">{{ execution.started_at }}</span>
                  <span>{{ execution.message }}</span>
                </span>
                <el-button link type="primary" @click="emit('execution', task.id, execution.id)">查看日志与目录树</el-button>
              </li>
            </ul>
            <el-button link type="primary" @click="emit('history', task.id)">全部历史</el-button>
          </div>
        </details>
      </article>
    </div>
  </details>
</template>

<style scoped>
.drama-group {
  min-width: 0;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--panel-radius);
  background: var(--card);
  container-type: inline-size;
}

.drama-group[open] {
  border-color: var(--el-color-primary-light-7);
}

.drama-group--failed,
.drama-group--failed[open] {
  border-inline-start: 3px solid var(--el-color-danger);
}

.drama-group__summary,
.drama-group-task__history > summary {
  list-style: none;
  cursor: pointer;
}

.drama-group__summary::-webkit-details-marker,
.drama-group-task__history > summary::-webkit-details-marker {
  display: none;
}

.drama-group__summary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px 14px;
  padding: 13px 16px;
  border-radius: inherit;
}

.drama-group__summary:hover,
.drama-group[open] > .drama-group__summary {
  background: var(--el-color-primary-light-9);
}

.drama-group[open] > .drama-group__summary {
  border-end-start-radius: 0;
  border-end-end-radius: 0;
}

summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -3px;
}

.drama-group__chevron {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
}

.drama-group__chevron svg,
.drama-group-task__history-arrow {
  width: 14px;
  height: 14px;
  flex: none;
}

.drama-group[open] > summary .drama-group__chevron svg,
.drama-group-task__history[open] > summary .drama-group-task__history-arrow {
  transform: rotate(90deg);
  color: var(--el-color-primary);
}

@media (prefers-reduced-motion: no-preference) {
  .drama-group__chevron svg,
  .drama-group-task__history-arrow {
    transition: transform 160ms ease;
  }
}

.drama-group__heading,
.drama-group-task__fields > div {
  min-width: 0;
}

.drama-group__heading strong {
  display: block;
  font-size: 16px;
  line-height: 1.4;
  overflow-wrap: anywhere;
  color: var(--el-text-color-primary);
}

.drama-group__meta,
.drama-group__status,
.drama-group__drives,
.drama-group-task__tags,
.drama-group-task__inline,
.drama-group-task__actions,
.drama-group-task__execution {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
}

.drama-group__meta {
  margin-top: 3px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.drama-group__status {
  justify-content: flex-end;
}

.drama-group__drives {
  gap: 4px;
}

.drama-group__count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.drama-group__body {
  display: grid;
  gap: 10px;
  padding: 10px 12px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
  border-radius: 0 0 var(--panel-radius) var(--panel-radius);
  background: var(--el-fill-color-lighter);
}

.drama-group-task {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  padding: 12px 14px;
  min-width: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: var(--card);
}

.drama-group-task__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}

.drama-group-task__name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin: 0;
  font-size: 14px;
  overflow-wrap: anywhere;
  color: var(--el-text-color-primary);
}

.drama-group-task__warning {
  margin: 0;
  padding: 6px 9px;
  border-radius: 6px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger-dark-2);
  font-size: 12px;
}

.drama-group-task__fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 8px 16px;
  margin: 0;
  font-size: 13px;
}

.drama-group-task__fields dt {
  margin-bottom: 3px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.drama-group-task__fields dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--el-text-color-regular);
}

.drama-group-task__inline a {
  min-width: 0;
  max-width: 100%;
  color: var(--el-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drama-group-task__inline a:hover {
  text-decoration: underline;
}

.drama-group-task__muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.drama-group-task__actions {
  gap: 6px;
}

.drama-group-task__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.drama-group-task__history {
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
}

.drama-group-task__history > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 3px 0;
  color: var(--el-text-color-regular);
}

.drama-group-task__history-body ul {
  display: grid;
  gap: 6px;
  margin: 0 0 6px;
  padding: 0;
  list-style: none;
}

.drama-group-task__history-body li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px 12px;
  padding: 7px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
}

.drama-group-task__execution {
  min-width: 0;
  overflow-wrap: anywhere;
}

@container (max-width: 650px) {
  .drama-group__summary {
    grid-template-columns: auto minmax(0, 1fr);
    gap: 8px 10px;
    padding: 12px;
  }

  .drama-group__status {
    grid-column: 2;
    justify-content: flex-start;
  }

  .drama-group-task__fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (max-width: 295px) {
  .drama-group__body {
    padding: 8px;
  }

  .drama-group-task__fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
