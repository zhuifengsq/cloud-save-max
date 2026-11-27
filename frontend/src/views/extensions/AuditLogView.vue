<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import CronPreviewDialog from '@/components/CronPreviewDialog.vue'
import ResponsivePagination from '@/components/ResponsivePagination.vue'
import { fetchAuditLogs, deleteAllAuditLogs, fetchAuditLogScheduler, updateAuditLogScheduler } from '@/api/audit'
import type { AuditLogItem } from '@/types/audit'
import { useIsMobile } from '@/composables/useIsMobile'
import { useAuthStore } from '@/stores/auth'
import { AUDIT_WRITE } from '@/constants/permissions'
import { formatDateTimeText } from '@/utils/datetime'
import { isImeComposing } from '@/utils/ime'
import { validateCrontab5, validateTimezone, describeCrontab } from '@/utils/cron'

const auth = useAuthStore()
const canWrite = computed(() => auth.permissions.includes(AUDIT_WRITE))

const loading = ref(false)
const items = ref<AuditLogItem[]>([])
const total = ref(0)
const isMobile = useIsMobile()
const activeTab = ref<'logs' | 'scheduler'>('logs')

const query = reactive({
  page: 1,
  page_size: 20,
  q: '',
  action: '',
  success: 'all' as 'all' | 'success' | 'failed',
})

const successParam = computed(() => {
  if (query.success === 'success') return true
  if (query.success === 'failed') return false
  return undefined
})

const scheduler = reactive({
  loading: false,
  saving: false,
  data: {
    enabled: false,
    crontab: '0 3 * * *',
    timezone: 'Asia/Shanghai',
    retention_days: 30,
  },
})

const cronPreviewVisible = ref(false)

async function loadData() {
  loading.value = true
  try {
    const data = await fetchAuditLogs({
      page: query.page,
      page_size: query.page_size,
      q: query.q || undefined,
      action: query.action || undefined,
      success: successParam.value,
    })
    items.value = data.items || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  query.q = ''
  query.action = ''
  query.success = 'all'
  query.page = 1
  loadData()
}

async function handleDeleteAll() {
  try {
    await ElMessageBox.confirm('确定删除所有审计日志？此操作不可恢复。', '删除确认', {
      type: 'warning',
      confirmButtonText: '全部删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    await deleteAllAuditLogs()
    ElMessage.success('已删除所有审计日志')
    loadData()
  } catch {}
}

async function loadScheduler() {
  scheduler.loading = true
  try {
    const data = await fetchAuditLogScheduler()
    scheduler.data.enabled = data.enabled
    scheduler.data.crontab = data.crontab
    scheduler.data.timezone = data.timezone
    scheduler.data.retention_days = data.retention_days
  } finally {
    scheduler.loading = false
  }
}

async function saveScheduler() {
  if (!validateCrontab5(scheduler.data.crontab)) {
    ElMessage.error('Cron 表达式格式错误，需要 5 段（分 时 日 月 周）')
    return
  }
  if (!validateTimezone(scheduler.data.timezone)) {
    ElMessage.error('时区格式错误')
    return
  }
  scheduler.saving = true
  try {
    await updateAuditLogScheduler({
      enabled: scheduler.data.enabled,
      crontab: scheduler.data.crontab,
      timezone: scheduler.data.timezone,
      retention_days: scheduler.data.retention_days,
    })
    ElMessage.success('已保存')
  } finally {
    scheduler.saving = false
  }
}

onMounted(() => {
  loadData()
  loadScheduler()
})
</script>

<template>
  <div class="shell-page">
    <div class="section-header">
      <div class="section-header__title">
        <h1>审计日志</h1>
        <div class="section-header__desc">记录登录、任务操作与配置变更，便于追溯。</div>
      </div>
      <div class="toolbar__right">
        <el-button type="primary" @click="loadData">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="audit-tabs">
      <el-tab-pane label="日志列表" name="logs">
        <section class="glass-panel filter-strip">
          <div class="toolbar">
            <div class="toolbar__left">
              <el-input
                v-model="query.q"
                clearable
                placeholder="搜索操作 / 用户 / 目标 / 详情"
                :style="{ width: isMobile ? '100%' : '300px' }"
                @keydown.enter="!isImeComposing($event) && loadData()"
                @clear="loadData"
              />
              <el-input
                v-model="query.action"
                clearable
                placeholder="精确操作类型（可选）"
                :style="{ width: isMobile ? '100%' : '240px' }"
                @keydown.enter="!isImeComposing($event) && loadData()"
                @clear="loadData"
              />
              <el-segmented
                v-model="query.success"
                @change="query.page = 1; loadData()"
                :options="[
                  { label: '全部', value: 'all' },
                  { label: '成功', value: 'success' },
                  { label: '失败', value: 'failed' },
                ]"
              />
            </div>
            <div class="toolbar__right">
              <el-button @click="resetFilters">清空筛选</el-button>
              <span v-if="canWrite" class="toolbar-divider" aria-hidden="true" />
              <el-button v-if="canWrite" type="danger" plain @click="handleDeleteAll">删除所有</el-button>
            </div>
          </div>
        </section>

        <section class="glass-panel table-panel">
          <div v-if="loading && !items.length" class="ui-skeleton-table" aria-hidden="true">
            <div v-for="i in 6" :key="`audit-sk-${i}`" class="ui-skeleton-table__row">
              <span class="ui-skeleton" style="width: 132px; height: 14px"></span>
              <span class="ui-skeleton" style="width: 78px; height: 14px"></span>
              <span class="ui-skeleton" style="width: 136px; height: 22px"></span>
              <span class="ui-skeleton" style="width: 96px; height: 14px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 52px; height: 22px"></span>
              <span class="ui-skeleton" style="flex: 1; height: 14px"></span>
            </div>
          </div>

          <div v-else-if="isMobile" class="mobile-record-list" v-loading="loading && Boolean(items.length)">
            <div v-if="!items.length" class="empty-state empty-state--compact">
              <div class="empty-state__title">暂无审计日志</div>
              <div class="empty-state__desc">当前筛选条件下没有记录，可清空筛选后再试。</div>
            </div>
            <article v-for="row in items" :key="row.id" class="mobile-record-card">
              <header class="mobile-record-card__head">
                <div class="mobile-record-card__head-main">
                  <code class="audit-action-pill">{{ row.action }}</code>
                </div>
                <el-tag :type="row.success ? 'success' : 'danger'" round size="small">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </header>
              <dl class="mobile-record-dl">
                <div class="mobile-record-dl__row">
                  <dt>时间</dt>
                  <dd class="audit-time">{{ formatDateTimeText(row.created_at) }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>用户</dt>
                  <dd>{{ row.actor_username || (row.actor_user_id ? `#${row.actor_user_id}` : '—') }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>目标</dt>
                  <dd>
                    <span>{{ row.target_type || '—' }}</span>
                    <span v-if="row.target_id" class="muted">:{{ row.target_id }}</span>
                  </dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>IP</dt>
                  <dd class="audit-ip">{{ row.ip || '—' }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>详情</dt>
                  <dd :class="{ muted: !row.detail }">{{ row.detail || '—' }}</dd>
                </div>
              </dl>
            </article>
          </div>

          <el-table v-else :data="items" border v-loading="loading && Boolean(items.length)">
            <template #empty>
              <div class="empty-state empty-state--compact">
                <div class="empty-state__title">暂无审计日志</div>
                <div class="empty-state__desc">当前筛选条件下没有记录，可清空筛选后再试。</div>
              </div>
            </template>
            <el-table-column label="时间" width="144">
              <template #default="{ row }">
                <span class="audit-time">{{ formatDateTimeText(row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="用户" width="96">
              <template #default="{ row }">
                <span>{{ row.actor_username || (row.actor_user_id ? `#${row.actor_user_id}` : '—') }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="180">
              <template #default="{ row }">
                <el-tooltip :content="row.action" placement="top" :trigger="['hover', 'focus']" :show-after="250">
                  <code class="audit-action-pill" tabindex="0">{{ row.action }}</code>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="目标" min-width="120">
              <template #default="{ row }">
                <span>{{ row.target_type || '—' }}</span>
                <span v-if="row.target_id" class="muted">:{{ row.target_id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="78">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" round>
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="IP" width="110">
              <template #default="{ row }">
                <span class="audit-ip">{{ row.ip || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="详情" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="row.detail">{{ row.detail }}</span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-bar">
            <ResponsivePagination
              :current-page="query.page"
              :page-size="query.page_size"
              :total="total"
              label="条日志"
              :mobile="isMobile"
              :disabled="loading"
              @current-change="(v) => ((query.page = v), loadData())"
            >
              <template #desktop>
                <el-pagination
                  v-model:current-page="query.page"
                  v-model:page-size="query.page_size"
                  background
                  layout="total, sizes, prev, pager, next"
                  :total="total"
                  @change="loadData"
                />
              </template>
            </ResponsivePagination>
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane label="定时清理" name="scheduler">
        <section class="glass-panel scheduler-panel" v-loading="scheduler.loading">
          <el-form label-width="100px" label-position="left">
            <el-form-item label="启用定时清理">
              <el-switch v-model="scheduler.data.enabled" />
            </el-form-item>
            <el-form-item label="Cron 表达式">
              <el-input v-model="scheduler.data.crontab" placeholder="0 3 * * *" style="width: 200px" />
              <el-button style="margin-left: 8px" @click="cronPreviewVisible = true">预览</el-button>
              <div class="form-tip">
                {{ describeCrontab(scheduler.data.crontab) }}
              </div>
            </el-form-item>
            <el-form-item label="时区">
              <el-input v-model="scheduler.data.timezone" placeholder="Asia/Shanghai" style="width: 200px" />
            </el-form-item>
            <el-form-item label="保留天数">
              <el-input-number v-model="scheduler.data.retention_days" :min="1" :max="3650" />
              <div class="form-tip">超过此天数的日志将被自动清理</div>
            </el-form-item>
            <el-form-item>
              <el-button v-if="canWrite" type="primary" :loading="scheduler.saving" @click="saveScheduler">保存</el-button>
            </el-form-item>
          </el-form>
        </section>

        <CronPreviewDialog
          v-model="cronPreviewVisible"
          title="Cron 执行预览"
          :crontab="scheduler.data.crontab"
          :timezone="scheduler.data.timezone"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.audit-tabs {
  margin-top: 0;
}

.audit-tabs .filter-strip {
  margin-bottom: 16px;
}

.table-panel {
  padding: 18px;
}

.scheduler-panel {
  padding: 24px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.audit-time,
.audit-ip {
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}

.audit-action-pill {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--surface-muted);
  border: 1px solid var(--line-soft);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  color: var(--ink);
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

@media (max-width: 767px) {
  .table-panel {
    padding: 14px;
  }
}
</style>
