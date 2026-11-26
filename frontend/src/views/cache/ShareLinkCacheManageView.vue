<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  clearInvalidShareLinks,
  clearSharePreviewBatchMemoryCache,
  deleteInvalidShareLink,
  deleteSharePreviewBatchCacheItem,
  fetchInvalidShareLinksList,
  fetchSharePreviewBatchCacheList,
  purgeSharePreviewBatchCache,
} from '@/api/shareLinkCache'
import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import { formatDateTimeText } from '@/utils/datetime'
import { DRIVE_TYPE_META, type DriveType } from '@/utils/driveType'
import { isImeComposing } from '@/utils/ime'
import type { InvalidShareLinkListItem, SharePreviewBatchCacheListItem } from '@/types/shareLinkCache'

function formatDriveLabel(driveType?: string | null) {
  if (!driveType) return '—'
  return DRIVE_TYPE_META[driveType as DriveType]?.label || driveType
}

const auth = useAuthStore()
const isMobile = useIsMobile()
const canWrite = computed(() => auth.permissions.includes(TASK_WRITE))

const activeTab = ref<'cache' | 'invalid'>('cache')

const cache = reactive({
  loading: false,
  items: [] as SharePreviewBatchCacheListItem[],
  total: 0,
  query: {
    page: 1,
    page_size: 20,
    q: '',
    drive_type: '',
    ok: 'all' as 'all' | 'ok' | 'fail',
    expired_only: false,
  },
})

const invalid = reactive({
  loading: false,
  items: [] as InvalidShareLinkListItem[],
  total: 0,
  query: {
    page: 1,
    page_size: 20,
    q: '',
    drive_type: '',
  },
})

const cacheOkParam = computed(() => {
  if (cache.query.ok === 'ok') return true
  if (cache.query.ok === 'fail') return false
  return undefined
})

async function loadCache() {
  cache.loading = true
  try {
    const data = await fetchSharePreviewBatchCacheList({
      page: cache.query.page,
      page_size: cache.query.page_size,
      q: cache.query.q || undefined,
      drive_type: cache.query.drive_type || undefined,
      ok: cacheOkParam.value,
      expired_only: cache.query.expired_only || undefined,
    })
    cache.items = data.items || []
    cache.total = data.total || 0
  } finally {
    cache.loading = false
  }
}

async function loadInvalid() {
  invalid.loading = true
  try {
    const data = await fetchInvalidShareLinksList({
      page: invalid.query.page,
      page_size: invalid.query.page_size,
      q: invalid.query.q || undefined,
      drive_type: invalid.query.drive_type || undefined,
    })
    invalid.items = data.items || []
    invalid.total = data.total || 0
  } finally {
    invalid.loading = false
  }
}

async function refreshActive() {
  if (activeTab.value === 'invalid') {
    await loadInvalid()
  } else {
    await loadCache()
  }
}

async function handleDeleteCacheItem(shareurl: string) {
  if (!canWrite.value) return
  const out = await deleteSharePreviewBatchCacheItem({ shareurl })
  ElMessage.success(`已删除 ${out.deleted || 0} 条`)
  await loadCache()
}

async function handleDeleteInvalidItem(shareurl: string) {
  if (!canWrite.value) return
  const out = await deleteInvalidShareLink({ shareurl })
  ElMessage.success(`已删除 ${out.deleted || 0} 条`)
  await loadInvalid()
}

async function handlePurgeExpired() {
  if (!canWrite.value) return
  const out = await purgeSharePreviewBatchCache({ expired_only: true, retention_seconds: 0 })
  ElMessage.success(`已清理 ${out.deleted || 0} 条过期缓存`)
  await loadCache()
}

async function handlePurgeRetention() {
  if (!canWrite.value) return
  const out = await purgeSharePreviewBatchCache({ expired_only: false, retention_seconds: 0 })
  ElMessage.success(`已清理 ${out.deleted || 0} 条历史缓存`)
  await loadCache()
}

async function handleClearMemoryCache() {
  if (!canWrite.value) return
  const out = await clearSharePreviewBatchMemoryCache()
  if (out.cleared) ElMessage.success('已清空进程内缓存（仅对当前后端进程生效）')
}

async function handleClearInvalidAll() {
  if (!canWrite.value) return
  await ElMessageBox.confirm('确认清空所有“永久异常 shareurl”？这会影响资源搜索/修复的失效过滤。', '清空确认', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
    confirmButtonClass: 'el-button--danger',
  })
  const out = await clearInvalidShareLinks({})
  ElMessage.success(`已清空 ${out.deleted || 0} 条`)
  await loadInvalid()
}

function resetCacheFilters() {
  cache.query.q = ''
  cache.query.drive_type = ''
  cache.query.ok = 'all'
  cache.query.expired_only = false
  cache.query.page = 1
  loadCache()
}

function resetInvalidFilters() {
  invalid.query.q = ''
  invalid.query.drive_type = ''
  invalid.query.page = 1
  loadInvalid()
}

onMounted(async () => {
  await loadCache()
  await loadInvalid()
})
</script>

<template>
  <div class="shell-page">
    <div class="section-header">
      <div class="section-header__title">
        <h1>分享链接缓存</h1>
        <div class="section-header__desc">预览与校验结果缓存，命中后免去重复解析。</div>
      </div>
      <div class="toolbar__right">
        <el-button type="primary" @click="refreshActive">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="cache-tabs">
      <el-tab-pane label="预览缓存" name="cache">
        <section class="glass-panel filter-strip">
          <div class="toolbar">
            <div class="toolbar__left">
              <el-input
                v-model="cache.query.q"
                clearable
                placeholder="搜索分享链接"
                :style="{ width: isMobile ? '100%' : '360px' }"
                @keydown.enter="!isImeComposing($event) && loadCache()"
                @clear="loadCache"
              />
              <el-input
                v-model="cache.query.drive_type"
                clearable
                placeholder="网盘类型（可选，如 quark）"
                :style="{ width: isMobile ? '100%' : '220px' }"
                @keydown.enter="!isImeComposing($event) && loadCache()"
                @clear="loadCache"
              />
              <el-segmented
                v-model="cache.query.ok"
                :options="[
                  { label: '全部', value: 'all' },
                  { label: '有效', value: 'ok' },
                  { label: '异常', value: 'fail' },
                ]"
              />
              <el-checkbox v-model="cache.query.expired_only" @change="loadCache">仅过期</el-checkbox>
            </div>
            <div class="toolbar__right">
              <el-button @click="resetCacheFilters">清空筛选</el-button>
              <el-button :disabled="!canWrite" @click="handleClearMemoryCache">清空内存缓存</el-button>
              <el-button :disabled="!canWrite" @click="handlePurgeExpired">清理过期</el-button>
              <el-button :disabled="!canWrite" @click="handlePurgeRetention">清理历史</el-button>
            </div>
          </div>
        </section>

        <section class="glass-panel table-panel">
          <div v-if="cache.loading && !cache.items.length" class="ui-skeleton-table" aria-hidden="true">
            <div v-for="i in 6" :key="`share-cache-sk-${i}`" class="ui-skeleton-table__row">
              <span class="ui-skeleton" style="flex: 1; height: 15px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 72px; height: 20px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 52px; height: 22px"></span>
              <span class="ui-skeleton" style="width: 120px; height: 14px"></span>
              <span class="ui-skeleton" style="width: 112px; height: 14px"></span>
            </div>
          </div>

          <div v-else-if="isMobile" class="mobile-record-list" v-loading="cache.loading && Boolean(cache.items.length)">
            <div v-if="!cache.items.length" class="empty-state empty-state--compact">
              <div class="empty-state__title">暂无分享链接缓存</div>
              <div class="empty-state__desc">搜索验证或解析分享链接后会自动写入缓存。</div>
            </div>
            <article v-for="row in cache.items" :key="row.shareurl" class="mobile-record-card">
              <header class="mobile-record-card__head">
                <div class="mobile-record-card__head-main">
                  <span v-if="row.drive_type" class="drive-cell">
                    <DriveTypeIcon :type="row.drive_type" :size="16" />
                    <span>{{ formatDriveLabel(row.drive_type) }}</span>
                  </span>
                  <span v-else class="muted">—</span>
                </div>
                <el-tag :type="row.ok ? 'success' : 'danger'" effect="plain" round size="small">
                  {{ row.ok ? '有效' : '异常' }}
                </el-tag>
              </header>
              <code class="share-url-cell mobile-record-code">{{ row.shareurl }}</code>
              <dl class="mobile-record-dl">
                <div class="mobile-record-dl__row">
                  <dt>解析说明</dt>
                  <dd :class="{ muted: !row.message }">{{ row.message || '—' }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>检查时间</dt>
                  <dd class="time-cell">{{ formatDateTimeText(row.checked_at) }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>过期时间</dt>
                  <dd class="time-cell">{{ formatDateTimeText(row.expires_at) }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>命中</dt>
                  <dd class="time-cell">{{ row.hit_count ?? 0 }}</dd>
                </div>
              </dl>
              <footer v-if="canWrite" class="mobile-record-card__actions">
                <el-button link type="danger" @click="handleDeleteCacheItem(row.shareurl)">删除</el-button>
              </footer>
            </article>
          </div>

          <el-table v-else :data="cache.items" border v-loading="cache.loading && Boolean(cache.items.length)">
            <template #empty>
              <div class="empty-state empty-state--compact">
                <div class="empty-state__title">暂无分享链接缓存</div>
                <div class="empty-state__desc">搜索验证或解析分享链接后会自动写入缓存。</div>
              </div>
            </template>
            <el-table-column label="分享链接" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="share-url-cell">{{ row.shareurl }}</code>
              </template>
            </el-table-column>
            <el-table-column label="网盘类型" width="116">
              <template #default="{ row }">
                <span v-if="row.drive_type" class="drive-cell">
                  <DriveTypeIcon :type="row.drive_type" :size="16" />
                  <span>{{ formatDriveLabel(row.drive_type) }}</span>
                </span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="78">
              <template #default="{ row }">
                <el-tag :type="row.ok ? 'success' : 'danger'" effect="plain" round>{{ row.ok ? '有效' : '异常' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column
              prop="message"
              label="解析说明"
              min-width="150"
              :show-overflow-tooltip="{
                appendTo: 'body',
                popperOptions: { strategy: 'fixed' },
              }"
            >
              <template #default="{ row }">
                <span v-if="row.message">{{ row.message }}</span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="检查时间" width="154">
              <template #default="{ row }">
                <span class="time-cell">{{ formatDateTimeText(row.checked_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="过期时间" width="154">
              <template #default="{ row }">
                <span class="time-cell">{{ formatDateTimeText(row.expires_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="命中" width="66" align="right">
              <template #default="{ row }">
                <span class="time-cell">{{ row.hit_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="76">
              <template #default="{ row }">
                <el-button link type="danger" :disabled="!canWrite" @click="handleDeleteCacheItem(row.shareurl)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-bar">
            <el-pagination
              v-model:current-page="cache.query.page"
              v-model:page-size="cache.query.page_size"
              background
              layout="total, sizes, prev, pager, next"
              :total="cache.total"
              @change="loadCache"
            />
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane label="永久异常" name="invalid">
        <section class="glass-panel filter-strip">
          <div class="toolbar">
            <div class="toolbar__left">
              <el-input
                v-model="invalid.query.q"
                clearable
                placeholder="搜索分享链接"
                :style="{ width: isMobile ? '100%' : '360px' }"
                @keydown.enter="!isImeComposing($event) && loadInvalid()"
                @clear="loadInvalid"
              />
              <el-input
                v-model="invalid.query.drive_type"
                clearable
                placeholder="网盘类型（可选，如 quark）"
                :style="{ width: isMobile ? '100%' : '220px' }"
                @keydown.enter="!isImeComposing($event) && loadInvalid()"
                @clear="loadInvalid"
              />
            </div>
            <div class="toolbar__right">
              <el-button @click="resetInvalidFilters">清空筛选</el-button>
              <span v-if="canWrite" class="toolbar-divider" aria-hidden="true" />
              <el-button type="danger" plain :disabled="!canWrite" @click="handleClearInvalidAll">清空全部</el-button>
            </div>
          </div>
        </section>

        <section class="glass-panel table-panel">
          <div v-if="invalid.loading && !invalid.items.length" class="ui-skeleton-table" aria-hidden="true">
            <div v-for="i in 6" :key="`share-invalid-sk-${i}`" class="ui-skeleton-table__row">
              <span class="ui-skeleton" style="flex: 1; height: 15px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 72px; height: 20px"></span>
              <span class="ui-skeleton" style="width: 150px; height: 14px"></span>
              <span class="ui-skeleton" style="width: 112px; height: 14px"></span>
            </div>
          </div>

          <div v-else-if="isMobile" class="mobile-record-list" v-loading="invalid.loading && Boolean(invalid.items.length)">
            <div v-if="!invalid.items.length" class="empty-state empty-state--compact">
              <div class="empty-state__title">暂无永久异常链接</div>
              <div class="empty-state__desc">当前没有被标记为永久失效的分享链接。</div>
            </div>
            <article v-for="row in invalid.items" :key="row.shareurl" class="mobile-record-card">
              <header class="mobile-record-card__head">
                <div class="mobile-record-card__head-main">
                  <span v-if="row.drive_type" class="drive-cell">
                    <DriveTypeIcon :type="row.drive_type" :size="16" />
                    <span>{{ formatDriveLabel(row.drive_type) }}</span>
                  </span>
                  <span v-else class="muted">—</span>
                </div>
                <span class="status-pill">命中 {{ row.hit_count ?? 0 }}</span>
              </header>
              <code class="share-url-cell mobile-record-code">{{ row.shareurl }}</code>
              <dl class="mobile-record-dl">
                <div class="mobile-record-dl__row">
                  <dt>失败原因</dt>
                  <dd :class="{ muted: !row.message }">{{ row.message || '—' }}</dd>
                </div>
                <div class="mobile-record-dl__row">
                  <dt>更新时间</dt>
                  <dd class="time-cell">{{ formatDateTimeText(row.updated_at) }}</dd>
                </div>
              </dl>
              <footer v-if="canWrite" class="mobile-record-card__actions">
                <el-button link type="danger" @click="handleDeleteInvalidItem(row.shareurl)">删除</el-button>
              </footer>
            </article>
          </div>

          <el-table v-else :data="invalid.items" border v-loading="invalid.loading && Boolean(invalid.items.length)">
            <template #empty>
              <div class="empty-state empty-state--compact">
                <div class="empty-state__title">暂无永久异常链接</div>
                <div class="empty-state__desc">当前没有被标记为永久失效的分享链接。</div>
              </div>
            </template>
            <el-table-column label="分享链接" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="share-url-cell">{{ row.shareurl }}</code>
              </template>
            </el-table-column>
            <el-table-column label="网盘类型" width="116">
              <template #default="{ row }">
                <span v-if="row.drive_type" class="drive-cell">
                  <DriveTypeIcon :type="row.drive_type" :size="16" />
                  <span>{{ formatDriveLabel(row.drive_type) }}</span>
                </span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="message"
              label="失败原因"
              min-width="180"
              :show-overflow-tooltip="{
                appendTo: 'body',
                popperOptions: { strategy: 'fixed' },
              }"
            >
              <template #default="{ row }">
                <span v-if="row.message">{{ row.message }}</span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="命中" width="66" align="right">
              <template #default="{ row }">
                <span class="time-cell">{{ row.hit_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="154">
              <template #default="{ row }">
                <span class="time-cell">{{ formatDateTimeText(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="76">
              <template #default="{ row }">
                <el-button link type="danger" :disabled="!canWrite" @click="handleDeleteInvalidItem(row.shareurl)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-bar">
            <el-pagination
              v-model:current-page="invalid.query.page"
              v-model:page-size="invalid.query.page_size"
              background
              layout="total, sizes, prev, pager, next"
              :total="invalid.total"
              @change="loadInvalid"
            />
          </div>
        </section>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.cache-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

.cache-tabs .filter-strip {
  margin-bottom: 16px;
}

.table-panel {
  padding: 18px;
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.share-url-cell {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  color: var(--ink);
}

.drive-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.time-cell {
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}

.muted {
  color: var(--ink-muted);
}

@media (max-width: 767px) {
  .table-panel {
    padding: 14px;
  }
}
</style>
