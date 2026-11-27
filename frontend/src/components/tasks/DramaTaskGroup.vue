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
  pinned: boolean
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
import { useId } from 'vue'

import { formatDateTimeText } from '@/utils/datetime'
import DriveTypeIcon from './DriveTypeIcon.vue'
import GeneratedShareStatus from './GeneratedShareStatus.vue'

defineProps<{
  group: DramaTaskGroupData
  open: boolean
  openExecutions: ReadonlySet<number>
  focusedTaskId?: number | null
  canRun: boolean
  canWrite: boolean
  pinBusy: boolean
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  'update:execution-open': [taskId: number, open: boolean]
  run: [task: TaskSummary]
  pin: []
  edit: [task: TaskSummary]
  command: [command: string, task: TaskSummary]
  copy: [task: TaskSummary]
  history: [taskId: number]
  execution: [taskId: number, executionId: number]
}>()

const panelId = useId()

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

function executionTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function executionTransferCount(execution: { adapter_snapshot?: Record<string, any> }) {
  const snap = execution.adapter_snapshot
  const listLen = Array.isArray(snap?.new_files) ? snap.new_files.length : 0
  return Math.max(0, Number(snap?.transfer_count) || 0, Number(snap?.new_files_count) || 0, listLen)
}
</script>

<template>
  <div
    class="drama-group"
    :class="{
      'drama-group--failed': group.failed > 0,
      'drama-group--pinned': group.pinned,
      'drama-group--open': open,
    }"
  >
    <div class="drama-group__summary" @click="emit('update:open', !open)">
      <span class="drama-group__chevron" aria-hidden="true"><ArrowRight /></span>
      <div class="drama-group__heading">
        <div class="drama-group__title">
          <h3>
            <button
              class="drama-group__toggle"
              type="button"
              :aria-expanded="open"
              :aria-controls="panelId"
            >
              <strong>{{ group.title }}</strong>
            </button>
          </h3>
          <button
            v-if="canWrite"
            class="drama-group__pin"
            type="button"
            :aria-label="`置顶 ${group.title}`"
            :aria-pressed="group.pinned"
            :aria-disabled="pinBusy"
            :title="group.pinned ? '取消置顶' : '置顶'"
            @click.stop="emit('pin')"
          >
            <svg class="drama-group__pin-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                v-if="group.pinned"
                fill="currentColor"
                d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1 0 .707c-.48.48-1.072.588-1.503.588-.177 0-.335-.018-.46-.039l-3.134 3.134a6 6 0 0 1 .16 1.013c.046.702-.032 1.687-.72 2.375a.5.5 0 0 1-.707 0l-2.829-2.828-3.182 3.182c-.195.195-1.219.902-1.414.707s.512-1.22.707-1.414l3.182-3.182-2.828-2.829a.5.5 0 0 1 0-.707c.688-.688 1.673-.767 2.375-.72a6 6 0 0 1 1.013.16l3.134-3.133a3 3 0 0 1-.04-.461c0-.43.108-1.022.589-1.503a.5.5 0 0 1 .353-.146"
              />
              <path
                v-else
                fill="none"
                stroke="currentColor"
                stroke-width="1.3"
                stroke-linejoin="round"
                d="M9.8 1.4 14.6 6.2c-.6.6-1.4.6-2 .5L9.5 9.8c.2.9.1 2-.6 2.8L6.1 9.8 2.8 13.1l-.9.1.1-.9L5.3 9 2.5 6.2c.8-.7 1.9-.8 2.8-.6l3.1-3.1c-.1-.6-.1-1.4.5-2Z"
              />
            </svg>
          </button>
          <span v-else-if="group.pinned" class="drama-group__pin-marker" role="img" aria-label="已置顶">
            <svg class="drama-group__pin-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                fill="currentColor"
                d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1 0 .707c-.48.48-1.072.588-1.503.588-.177 0-.335-.018-.46-.039l-3.134 3.134a6 6 0 0 1 .16 1.013c.046.702-.032 1.687-.72 2.375a.5.5 0 0 1-.707 0l-2.829-2.828-3.182 3.182c-.195.195-1.219.902-1.414.707s.512-1.22.707-1.414l3.182-3.182-2.828-2.829a.5.5 0 0 1 0-.707c.688-.688 1.673-.767 2.375-.72a6 6 0 0 1 1.013.16l3.134-3.133a3 3 0 0 1-.04-.461c0-.43.108-1.022.589-1.503a.5.5 0 0 1 .353-.146"
              />
            </svg>
          </span>
          <el-tag v-if="group.pinned" size="small" effect="plain" round>置顶</el-tag>
        </div>
        <span class="drama-group__meta">
          <span>{{ group.items.length }} 个任务</span>
          <span>{{ group.tmdbId ? `TMDB ${mediaLabel(group.mediaType)} #${group.tmdbId}` : '未关联 TMDB' }}</span>
        </span>
      </div>
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
    </div>

    <div v-show="open" :id="panelId" class="drama-group__body">
      <article
        v-for="{ task, drive, account, share, leadIn, leadInTime, linkFailure } in open ? group.items : []"
        :key="task.id"
        :id="`drama-task-${task.id}`"
        class="drama-group-task"
        :class="{ 'drama-group-task--focused': focusedTaskId === task.id }"
        :tabindex="focusedTaskId === task.id ? -1 : undefined"
        :aria-label="focusedTaskId === task.id ? task.taskname : undefined"
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
                  <el-tag size="small" :type="executionTagType(execution.status)" round>
                    {{ executionLabels[execution.status] || execution.status }}
                  </el-tag>
                  <el-button
                    v-if="execution.started_at"
                    link
                    type="primary"
                    :aria-label="`${formatDateTimeText(execution.started_at)}，查看日志与目录树`"
                    aria-haspopup="dialog"
                    @click="emit('execution', task.id, execution.id)"
                  >
                    {{ formatDateTimeText(execution.started_at) }}
                  </el-button>
                  <span>{{ execution.message }}</span>
                  <el-tag
                    v-if="execution.status === 'success' && executionTransferCount(execution) > 0"
                    size="small"
                    type="success"
                    effect="plain"
                    round
                    disable-transitions
                  >
                    +{{ executionTransferCount(execution) }} 新存入
                  </el-tag>
                </span>
                <el-button link type="primary" @click="emit('execution', task.id, execution.id)">查看日志与目录树</el-button>
              </li>
            </ul>
            <el-button link type="primary" @click="emit('history', task.id)">全部历史</el-button>
          </div>
        </details>
      </article>
    </div>
  </div>
</template>

<style scoped>
.drama-group {
  min-width: 0;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--panel-radius);
  background: var(--card);
  container-type: inline-size;
}

.drama-group--open {
  border-color: var(--el-color-primary-light-7);
}

.drama-group--failed {
  border-inline-start: 3px solid var(--el-color-danger);
}

.drama-group--pinned:not(.drama-group--failed) {
  border-inline-start: 3px solid var(--brand);
}

.drama-group-task__history > summary {
  list-style: none;
  cursor: pointer;
}

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
  cursor: pointer;
}

.drama-group__title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.drama-group__title h3 {
  min-width: 0;
  margin: 0;
}

.drama-group__toggle {
  display: block;
  max-inline-size: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  font: inherit;
  text-align: start;
  cursor: pointer;
}

.drama-group__pin,
.drama-group__pin-marker {
  display: grid;
  flex: none;
  place-items: center;
  inline-size: 26px;
  block-size: 26px;
  border-radius: 6px;
  font-size: 14px;
}

.drama-group__pin {
  padding: 0;
  border: 0;
  background: transparent;
  font-family: inherit;
  line-height: 1;
  cursor: pointer;
}

.drama-group__pin[aria-pressed='false'] .drama-group__pin-icon {
  color: var(--ink-2);
  opacity: 0.82;
}

.drama-group__pin[aria-pressed='false']:hover .drama-group__pin-icon {
  opacity: 1;
  color: var(--brand);
}

.drama-group__pin[aria-pressed='true'] .drama-group__pin-icon,
.drama-group__pin-marker .drama-group__pin-icon {
  color: var(--el-color-primary);
}

.drama-group__pin:hover,
.drama-group__pin:focus-visible,
.drama-group__pin[aria-pressed='true'],
.drama-group__pin-marker {
  background: var(--el-color-primary-light-8);
}

.drama-group__pin[aria-disabled='true'] {
  opacity: 0.65;
  cursor: wait;
}

.drama-group__summary:hover,
.drama-group--open > .drama-group__summary {
  background: var(--el-color-primary-light-9);
}

.drama-group--open > .drama-group__summary {
  border-end-start-radius: 0;
  border-end-end-radius: 0;
}

summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -3px;
}

.drama-group__toggle:focus-visible,
.drama-group__pin:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.drama-group__chevron {
  display: grid;
  align-self: start;
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

.drama-group--open > .drama-group__summary .drama-group__chevron svg,
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

@media (pointer: coarse) {
  .drama-group__chevron {
    margin-block-start: 4px;
  }

  .drama-group__pin {
    inline-size: 44px;
    block-size: 44px;
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

.drama-group-task--focused {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
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

.drama-group-task__inline {
  flex-wrap: nowrap;
  min-width: 0;
}

.drama-group-task__inline > :not(a) {
  flex-shrink: 0;
}

.drama-group-task__inline a {
  flex: 0 1 auto;
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

@supports (interpolate-size: allow-keywords) {
  @media (prefers-reduced-motion: no-preference) {
    .drama-group-task__history::details-content {
      block-size: 0;
      opacity: 0;
      overflow: hidden;
      transition: block-size 0.22s ease, opacity 0.2s ease, content-visibility 0.22s ease;
      transition-behavior: allow-discrete;
    }

    .drama-group-task__history[open]::details-content {
      block-size: auto;
      opacity: 1;
    }
  }
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
