<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { clearProxyImageCache, fetchProxyImageCacheStats, purgeProxyImageCache } from '@/api/proxyImageCache'
import { useIsMobile } from '@/composables/useIsMobile'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { ProxyImageCacheStats } from '@/types/proxyImageCache'

const auth = useAuthStore()
const isMobile = useIsMobile()
const canWrite = computed(() => auth.permissions.includes(TASK_WRITE))

const loading = ref(false)
const stats = ref<ProxyImageCacheStats | null>(null)

function formatBytes(value: number) {
  const bytes = Number(value || 0)
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let num = bytes
  let idx = 0
  while (num >= 1024 && idx < units.length - 1) {
    num /= 1024
    idx += 1
  }
  return `${num.toFixed(idx === 0 ? 0 : 2)} ${units[idx]}`
}

function formatDuration(seconds: number) {
  const secs = Number(seconds || 0)
  if (!secs) return '0 秒'
  if (secs >= 86400) return `${(secs / 86400).toFixed(1).replace(/\.0$/, '')} 天（${secs} 秒）`
  if (secs >= 3600) return `${(secs / 3600).toFixed(1).replace(/\.0$/, '')} 小时（${secs} 秒）`
  if (secs >= 60) return `${Math.round(secs / 60)} 分钟`
  return `${secs} 秒`
}

const usageRatio = computed(() => {
  if (!stats.value || !stats.value.max_total_bytes) return 0
  return Math.min(100, Math.round((Number(stats.value.total_bytes || 0) / Number(stats.value.max_total_bytes)) * 100))
})

async function loadData() {
  loading.value = true
  try {
    stats.value = await fetchProxyImageCacheStats()
  } finally {
    loading.value = false
  }
}

async function handlePurge() {
  if (!canWrite.value) return
  const out = await purgeProxyImageCache()
  ElMessage.success(`已清理 ${out.deleted_files || 0} 项（${formatBytes(out.deleted_bytes || 0)}）`)
  await loadData()
}

async function handleClear() {
  if (!canWrite.value) return
  await ElMessageBox.confirm('确认清空代理图片缓存目录？', '清空确认', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
  })
  const out = await clearProxyImageCache()
  ElMessage.success(`已清空 ${out.deleted_files || 0} 项（${formatBytes(out.deleted_bytes || 0)}）`)
  await loadData()
}

onMounted(loadData)
</script>

<template>
  <div class="shell-page">
    <div class="section-header">
      <div class="section-header__title">
        <h1>代理图片缓存</h1>
        <div class="section-header__desc">缓存 TMDB 海报等远程图片，减少外链加载失败。</div>
      </div>
      <div class="toolbar__right">
        <el-button type="primary" @click="loadData">刷新</el-button>
      </div>
    </div>

    <template v-if="stats">
      <section class="metric-strip proxy-metrics" v-loading="loading">
        <div class="glass-panel metric-tile">
          <div class="metric-tile__label">功能状态</div>
          <div class="metric-tile__value proxy-status-value">
            <el-tag :type="stats.enabled ? 'success' : 'info'" effect="plain" round size="large">
              {{ stats.enabled ? '已启用' : '未启用' }}
            </el-tag>
          </div>
          <div class="metric-tile__hint">过期时间 {{ formatDuration(stats.ttl_seconds) }}</div>
        </div>
        <div class="glass-panel metric-tile">
          <div class="metric-tile__label">已缓存文件</div>
          <div class="metric-tile__value">{{ stats.total_files }}</div>
          <div class="metric-tile__hint">当前本地缓存图片总数</div>
        </div>
        <div class="glass-panel metric-tile">
          <div class="metric-tile__label">占用空间</div>
          <div class="metric-tile__value">{{ formatBytes(stats.total_bytes) }}</div>
          <div class="metric-tile__hint">上限 {{ formatBytes(stats.max_total_bytes) }}（{{ usageRatio }}%）</div>
          <el-progress
            :percentage="usageRatio"
            :show-text="false"
            :stroke-width="6"
            :status="usageRatio >= 90 ? 'warning' : undefined"
            class="proxy-metric-bar"
          />
        </div>
        <div class="glass-panel metric-tile">
          <div class="metric-tile__label">已过期文件</div>
          <div class="metric-tile__value">{{ stats.stale_files }}</div>
          <div class="metric-tile__hint">{{ stats.stale_files > 0 ? '建议执行清理释放空间' : '当前无过期缓存' }}</div>
        </div>
      </section>

      <section class="glass-panel table-panel" v-loading="loading">
        <el-descriptions :column="isMobile ? 1 : 2" border>
          <el-descriptions-item label="缓存状态">
            <el-tag :type="stats.enabled ? 'success' : 'info'" effect="plain" round size="small">
              {{ stats.enabled ? '已启用' : '未启用' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="缓存有效期">{{ formatDuration(stats.ttl_seconds) }}</el-descriptions-item>
          <el-descriptions-item label="单文件上限">{{ formatBytes(stats.max_file_bytes) }}</el-descriptions-item>
          <el-descriptions-item label="总容量上限">{{ formatBytes(stats.max_total_bytes) }}</el-descriptions-item>
          <el-descriptions-item label="已缓存文件数">{{ stats.total_files }} 个</el-descriptions-item>
          <el-descriptions-item label="已占用空间">{{ formatBytes(stats.total_bytes) }}</el-descriptions-item>
          <el-descriptions-item label="已过期文件数">{{ stats.stale_files }} 个</el-descriptions-item>
          <el-descriptions-item label="缓存目录" :span="isMobile ? 1 : 2">
            <code class="cache-path">{{ stats.cache_dir || '—' }}</code>
          </el-descriptions-item>
        </el-descriptions>

        <div class="actions">
          <el-button :disabled="!canWrite" @click="handlePurge">清理过期</el-button>
          <el-button type="danger" :disabled="!canWrite" @click="handleClear">一键清空</el-button>
        </div>
      </section>
    </template>

    <section v-else class="glass-panel table-panel" v-loading="loading">
      <div class="empty-state">
        <div class="empty-state__title">暂无缓存统计数据</div>
        <div class="empty-state__desc">点击右上角刷新重新拉取本地图片缓存统计。</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.proxy-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.proxy-status-value {
  display: flex;
  align-items: center;
  min-height: 36px;
}

.proxy-metric-bar {
  margin-top: 8px;
}

.table-panel {
  padding: 18px;
}

.cache-path {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  color: var(--ink);
  word-break: break-all;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}

@media (max-width: 1024px) {
  .proxy-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .proxy-metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

