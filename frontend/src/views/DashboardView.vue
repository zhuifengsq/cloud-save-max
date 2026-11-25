<script setup lang="ts">
import { Calendar, CircleCheck, CirclePlus, Collection, Film, Odometer, VideoPlay } from '@element-plus/icons-vue'
import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'
import { DRIVE_TYPE_META, type DriveType } from '@/utils/driveType'
import { ElMessage, ElMessageBox } from 'element-plus'

import CapacityAccountCard from '@/components/dashboard/CapacityAccountCard.vue'
import CapacityHero from '@/components/dashboard/CapacityHero.vue'
import DashboardLogList from '@/components/dashboard/DashboardLogList.vue'
import DramaTrend from '@/components/dashboard/DramaTrend.vue'
import ShareLinkHistory from '@/components/dashboard/ShareLinkHistory.vue'
import FailureReason from '@/components/dashboard/FailureReason.vue'
import { failureStageLabel } from '@/utils/failureMessage'
import { clearDramaFailures, fetchCapacityOverview, fetchDramaOverview } from '@/api/dashboard'
import { refreshDriveAccountProfiles } from '@/api/extensions'
import { loadSharedTasks } from '@/stores/taskCache'
import {
  TMDB_DETAIL_REFRESH_MS,
  ensureTmdbDetails,
  invalidateTmdbDetails,
  tmdbDetailIdsNeedingRefresh,
  tmdbDetailsById,
} from '@/stores/tmdbDetailCache'
import { getTasksVersion, subscribeTasksChanged } from '@/utils/taskChangeBus'
import type { CapacityOverview, DramaOverview } from '@/types/dashboard'
import type { TaskOverview } from '@/types/tasks'
import { formatBytes, formatDateTime, formatPercent } from '@/utils/capacity'
import { findUpcomingAiring } from '@/utils/dramaSchedule'

const dtfBeijing = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

defineOptions({ name: 'DashboardView' })

const DRAMA_REFRESH_MS = 60 * 1000
const CAPACITY_REFRESH_MS = 5 * 60 * 1000
const CHECK_TICK_MS = 60 * 1000
const LIST_DISPLAY_LIMIT = 10

const dramaDays = ref(30)
const dramaLoading = ref(false)
const dramaRefreshing = ref(false)
const dramaOverview = ref<DramaOverview | null>(null)
const dramaTasks = ref<TaskOverview[]>([])
const tmdbLoading = ref(false)
const trendDialogVisible = ref(false)
const showAllFailures = ref(false)

const capacityLoading = ref(false)
const capacityRefreshing = ref(false)
const capacityOverview = ref<CapacityOverview | null>(null)

const accountGroups = computed(() => {
  const groups = new Map<string, CapacityOverview['accounts']>()
  for (const account of capacityOverview.value?.accounts || []) {
    const key = account.profile?.drive_name || account.drive_type
    const current = groups.get(key) || []
    current.push(account)
    groups.set(key, current)
  }
  return Array.from(groups.entries()).map(([name, accounts]) => ({ name, accounts }))
})

const hasAccounts = computed(() => Boolean(capacityOverview.value?.summary.account_count))
const hasCapacityData = computed(() => Boolean(capacityOverview.value?.summary.capacity_account_count))

function beijingDateStr(d: Date) {
  return dtfBeijing.format(d)
}

function addDays(d: Date, delta: number) {
  const copy = new Date(d)
  copy.setDate(copy.getDate() + delta)
  return copy
}

async function loadDrama(opts: { silent?: boolean } = {}) {
  const silent = opts.silent === true
  const versionAtStart = getTasksVersion()
  if (!silent) dramaLoading.value = true
  try {
    // 任务列表与追剧日历共用缓存，这里始终取最新一份，避免看到别处已删除的任务
    const [overviewData, allTasks] = await Promise.all([
      fetchDramaOverview(dramaDays.value),
      loadSharedTasks({ maxAgeMs: 0 }),
    ])
    dramaOverview.value = overviewData
    dramaTasks.value = allTasks.filter((t) => t.task_type === 'drama')
    dramaLoadedAt = Date.now()
    loadedTasksVersion = versionAtStart
  } catch (e: any) {
    // 静默刷新失败时保留原有内容，不打搅用户
    if (!silent) ElMessage.error(e?.response?.data?.message || e?.message || '追剧数据加载失败')
  } finally {
    if (!silent) dramaLoading.value = false
  }
}

const clearingFailures = ref(false)

async function clearFailures() {
  try {
    await ElMessageBox.confirm(
      '将删除所有追剧任务的失败执行记录，失败率和趋势统计会同步变化，且无法恢复。',
      '清理失败记录',
      { type: 'warning', confirmButtonText: '全部清理', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
  } catch {
    return
  }
  clearingFailures.value = true
  try {
    const res = await clearDramaFailures(0)
    showAllFailures.value = false
    ElMessage.success(`已清理 ${res.deleted} 条失败记录`)
    await refreshDrama({ silent: true })
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || e?.message || '清理失败')
  } finally {
    clearingFailures.value = false
  }
}

async function handleRefreshDrama() {
  dramaRefreshing.value = true
  try {
    // 手动刷新时丢弃 TMDB 资料缓存，之前失败过的剧集也一并重试
    invalidateTmdbDetails()
    await refreshDrama({ forceDetails: true })
    ElMessage.success('追剧数据已刷新')
  } finally {
    dramaRefreshing.value = false
  }
}

let tmdbPending = 0
let dramaRefreshPromise: Promise<void> | null = null
let dramaLoadedAt = 0
let capacityLoadedAt = 0
let loadedTasksVersion = -1
let dashboardActive = false
let firstActivated = true
let checkTimer: number | null = null
let changeTimer: number | null = null
let unsubscribeTasksChanged: (() => void) | null = null

function dramaTmdbIds() {
  return Array.from(
    new Set(
      dramaTasks.value
        .filter((t) => String(t.tmdb_media_type || '').toLowerCase() === 'tv')
        .map((t) => Number(t.tmdb_id) || 0)
        .filter((id) => id > 0),
    ),
  )
}

// TMDB 资料与追剧日历共用缓存，独立 30 分钟有效期。
// 拉取失败不会写入缓存，下次调用（包括“刷新追剧数据”）会重新尝试。
async function loadTMDBDetails(opts: { force?: boolean } = {}) {
  const ids = dramaTmdbIds()
  if (!ids.length) return
  tmdbPending += 1
  tmdbLoading.value = true
  try {
    await ensureTmdbDetails(ids, {
      force: opts.force === true,
      maxAgeMs: TMDB_DETAIL_REFRESH_MS,
      concurrency: 4,
    })
  } finally {
    tmdbPending -= 1
    tmdbLoading.value = tmdbPending > 0
  }
}

// 同一时间只跑一次追剧数据刷新；手动刷新会排在静默刷新之后重跑
function refreshDrama(opts: { silent?: boolean; forceDetails?: boolean } = {}): Promise<void> {
  if (dramaRefreshPromise) {
    if (opts.silent) return dramaRefreshPromise
    return dramaRefreshPromise.catch(() => {}).then(() => refreshDrama(opts))
  }
  dramaRefreshPromise = (async () => {
    await loadDrama(opts)
    // 追剧统计先显示，TMDB 资料随后补齐，不让整块区域一起等待
    await loadTMDBDetails({ force: opts.forceDetails })
  })().finally(() => {
    dramaRefreshPromise = null
  })
  return dramaRefreshPromise
}

function nextAiringForTask(task: TaskOverview, todayStr: string, maxDateStr: string) {
  const tmdbId = Number(task.tmdb_id) || 0
  if (!tmdbId) return null
  const detail = tmdbDetailsById[tmdbId] || null
  const data: any = detail?.data || null
  if (!data) return null
  return findUpcomingAiring(data, detail?.update_weekdays, todayStr, maxDateStr)
}

const contentSummary = computed(() => {
  const todayStr = beijingDateStr(new Date())
  const next7Str = beijingDateStr(addDays(new Date(), 7))

  let withNextAirDateCount = 0
  let todayCount = 0

  for (const task of dramaTasks.value) {
    if (!task.enabled) continue
    if (String(task.tmdb_media_type || '').toLowerCase() !== 'tv') continue
    const next = nextAiringForTask(task, todayStr, next7Str)
    if (!next) continue
    withNextAirDateCount += 1
    if (next.air_date === todayStr) todayCount += 1
  }

  return {
    with_next_air_date_count: withNextAirDateCount,
    today_count: todayCount,
    next7d_count: withNextAirDateCount,
  }
})

const activeTaskCount = computed(() => dramaTasks.value.filter((t) => t.enabled).length)

function titleFromTask(task: TaskOverview) {
  const tmdbId = Number(task.tmdb_id) || 0
  const detail = tmdbId ? tmdbDetailsById[tmdbId] || null : null
  const data: any = detail?.data || null
  return String(data?.name || data?.title || '').trim() || String(task.taskname || '').trim() || `任务 #${task.id}`
}

function timeAgoFromIso(iso?: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  const s = Math.max(0, Math.floor(diff / 1000))
  if (s < 60) return `${s}秒前`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}小时前`
  const day = Math.floor(h / 24)
  return `${day}天前`
}

function relativeDayLabel(isoDate: string) {
  // 与卡片窗口一致，按北京时间的“今天”比较，避免浏览器时区导致差一天
  const target = Date.parse(`${isoDate}T00:00:00Z`)
  if (Number.isNaN(target)) return isoDate
  const [y, m, d] = beijingDateStr(new Date()).split('-').map((x) => Number(x))
  const today = Date.UTC(y, (m || 1) - 1, d || 1)
  const diff = Math.round((target - today) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === 2) return '后天'
  if (diff > 0) return `${diff}天后`
  return isoDate
}

const taskSuccessRate = computed(() => {
  if (!dramaOverview.value) return null
  const s = dramaOverview.value.summary
  if (!s) return 1
  const success = Number(s.execution_success) || 0
  const failed = Number(s.execution_failed) || 0
  const total = success + failed
  if (!total) return 1
  return success / total
})

const updateProgressSummary = computed(() => {
  const tasks = dramaTasks.value.filter((t) => t.tmdb_id && String(t.tmdb_media_type || '').toLowerCase() === 'tv')
  const linked = tasks.length
  let latest = 0
  let behind = 0
  let unknown = 0
  let behindTotal = 0
  let behindMax = 0
  for (const task of tasks) {
    const p = task.drama_update_progress
    if (!p || !p.available) {
      unknown += 1
      continue
    }
    if (p.is_latest) {
      latest += 1
      continue
    }
    const n = typeof p.behind_episodes === 'number' ? p.behind_episodes : null
    if (n === null) {
      unknown += 1
      continue
    }
    if (n <= 0) {
      latest += 1
      continue
    }
    behind += 1
    behindTotal += n
    behindMax = Math.max(behindMax, n)
  }
  const ratio = linked > 0 ? latest / linked : null
  const avgBehind = behind > 0 ? behindTotal / behind : null
  return { linked, latest, behind, unknown, ratio, avgBehind, behindMax }
})

const updateProgressText = computed(() => {
  const r = updateProgressSummary.value.ratio
  if (r === null) return '--'
  return formatPercent(r)
})

const successRateText = computed(() => {
  const v = taskSuccessRate.value
  if (v === null || v === undefined || Number.isNaN(v)) {
    if (dramaTasks.value.length) return '100%'
    return '--'
  }
  return formatPercent(v)
})

const monthSuccessCount = computed(() => dramaOverview.value?.summary.monthly_success_count || 0)

const capacitySpaceText = computed(() => {
  const s = capacityOverview.value?.summary
  if (!s) return '--'
  const used = formatBytes(s.total_used_space)
  const total = formatBytes(s.total_space)
  if (used === '--' || total === '--') return '--'
  return `${used} / ${total}`
})

const recentUpdates = computed(() => {
  const tasksById = new Map(dramaTasks.value.map((task) => [task.id, task]))
  return (dramaOverview.value?.recent_updates ?? []).flatMap((item) => {
    const task = tasksById.get(item.task_id)
    return task ? [{ ...item, task }] : []
  })
})

const upcomingAiring = computed(() => {
  const today = beijingDateStr(new Date())
  const maxDate = beijingDateStr(addDays(new Date(), 7))
  // 每部剧只保留最近一次播出，避免同一部剧出现多行
  const byDrama = new Map<number, {
    task: TaskOverview
    air_date: string
    episode: string
    predicted: boolean
  }>()

  for (const task of dramaTasks.value) {
    if (!task.enabled) continue
    if (String(task.tmdb_media_type || '').toLowerCase() !== 'tv') continue
    const next = nextAiringForTask(task, today, maxDate)
    if (!next) continue
    const dramaKey = Number(task.tmdb_id) > 0 ? Number(task.tmdb_id) : task.id
    const current = byDrama.get(dramaKey)
    if (!current || next.air_date < current.air_date) {
      byDrama.set(dramaKey, { task, air_date: next.air_date, episode: next.episode, predicted: next.predicted })
    }
  }

  return Array.from(byDrama.values()).sort((a, b) => {
    const byDate = a.air_date.localeCompare(b.air_date)
    if (byDate !== 0) return byDate
    return titleFromTask(a.task).localeCompare(titleFromTask(b.task), 'zh-Hans-CN')
  })
})

const displayedUpcomingAiring = computed(() => upcomingAiring.value.slice(0, LIST_DISPLAY_LIMIT))

const failures = computed(() => dramaOverview.value?.recent_failures || [])

const trendPoints = computed(() => dramaOverview.value?.trend || [])

// 容量概览只读取后端已保存的快照，不会真的去查询网盘；
// 真正查询网盘仍然只由“刷新容量”按钮或后台调度触发。
async function loadCapacityOverview(opts: { silent?: boolean } = {}) {
  const silent = opts.silent === true
  if (!silent) capacityLoading.value = true
  try {
    capacityOverview.value = await fetchCapacityOverview()
    capacityLoadedAt = Date.now()
  } catch (e: any) {
    if (!silent) ElMessage.error(e?.response?.data?.message || e?.message || '容量数据加载失败')
  } finally {
    if (!silent) capacityLoading.value = false
  }
}

async function handleRefreshCapacity() {
  capacityRefreshing.value = true
  try {
    await refreshDriveAccountProfiles()
    await loadCapacityOverview({ silent: true })
    ElMessage.success('容量快照已刷新')
  } finally {
    capacityRefreshing.value = false
  }
}

function stopAutoCheck() {
  if (checkTimer !== null) {
    window.clearInterval(checkTimer)
    checkTimer = null
  }
}

function refreshStaleDetails() {
  const ids = dramaTmdbIds()
  if (!ids.length) return
  if (!tmdbDetailIdsNeedingRefresh(ids, TMDB_DETAIL_REFRESH_MS).length) return
  void loadTMDBDetails()
}

// 页面可见时每分钟检查一次：统计类数据 60 秒、TMDB 资料 30 分钟、容量快照 5 分钟
function startAutoCheck() {
  stopAutoCheck()
  checkTimer = window.setInterval(() => {
    if (!dashboardActive) return
    if (document.visibilityState !== 'visible') return
    if (Date.now() - dramaLoadedAt >= DRAMA_REFRESH_MS) void refreshDrama({ silent: true })
    if (Date.now() - capacityLoadedAt >= CAPACITY_REFRESH_MS) void loadCapacityOverview({ silent: true })
    refreshStaleDetails()
  }, CHECK_TICK_MS)
}

// 从别的页面切回来、或标签页重新可见时，只补该补的，不重复拉刚取过的数据
function catchUpIfStale() {
  if (getTasksVersion() !== loadedTasksVersion || Date.now() - dramaLoadedAt >= DRAMA_REFRESH_MS) {
    void refreshDrama({ silent: true })
  }
  if (Date.now() - capacityLoadedAt >= CAPACITY_REFRESH_MS) void loadCapacityOverview({ silent: true })
  refreshStaleDetails()
}

function handleVisibilityChange() {
  if (!dashboardActive) return
  if (document.visibilityState === 'visible') {
    startAutoCheck()
    catchUpIfStale()
    return
  }
  stopAutoCheck()
}

// 任务增删改/执行完成后立即刷新相关数据，合并短时间内的多次写操作
function scheduleDramaRefresh() {
  if (changeTimer !== null) window.clearTimeout(changeTimer)
  changeTimer = window.setTimeout(() => {
    changeTimer = null
    if (!dashboardActive || document.visibilityState !== 'visible') return
    void refreshDrama({ silent: true })
  }, 300)
}

function onShareLogsCleared() {
  void refreshDrama({ silent: true })
}

watch(
  () => dramaDays.value,
  () => {
    void refreshDrama()
  },
)

onMounted(() => {
  void refreshDrama()
  void loadCapacityOverview()
  startAutoCheck()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onActivated(() => {
  dashboardActive = true
  if (!unsubscribeTasksChanged) {
    unsubscribeTasksChanged = subscribeTasksChanged(() => {
      if (!dashboardActive) return
      scheduleDramaRefresh()
    })
  }
  // 首次挂载时 onMounted 已经加载过，不用再来一次
  if (firstActivated) {
    firstActivated = false
    return
  }
  startAutoCheck()
  catchUpIfStale()
})

onDeactivated(() => {
  dashboardActive = false
  stopAutoCheck()
  if (changeTimer !== null) {
    window.clearTimeout(changeTimer)
    changeTimer = null
  }
})

onBeforeUnmount(() => {
  dashboardActive = false
  stopAutoCheck()
  if (changeTimer !== null) {
    window.clearTimeout(changeTimer)
    changeTimer = null
  }
  unsubscribeTasksChanged?.()
  unsubscribeTasksChanged = null
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div class="dashboard">
    <div class="section-header">
      <div class="section-header__title">
        <div class="section-header__eyebrow">Dashboard</div>
        <h1>追剧仪表盘</h1>
        <div class="section-header__desc">
          <span>活跃订阅 {{ activeTaskCount }}</span>
          <span class="dot">·</span>
          <span>即将更新 {{ contentSummary.next7d_count }}</span>
          <span class="dot">·</span>
          <span>任务成功率 {{ successRateText }}</span>
          <span class="dot">·</span>
          <span>容量预警 {{ capacityOverview?.warning_accounts.length || 0 }}</span>
        </div>
      </div>
      <div class="toolbar__right">
        <el-radio-group v-model="dramaDays" size="small">
          <el-radio-button :value="7">近7天</el-radio-button>
          <el-radio-button :value="30">近30天</el-radio-button>
        </el-radio-group>
        <el-button type="primary" :loading="dramaRefreshing" @click="handleRefreshDrama">刷新追剧数据</el-button>
      </div>
    </div>

    <section class="metric-strip dashboard-metrics" v-loading="dramaLoading">
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">活跃订阅</div>
          <span class="metric-tile__icon metric-tile__icon--brand" aria-hidden="true">
            <el-icon><Collection /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ activeTaskCount }}</div>
        <div class="metric-tile__hint">
          <span v-if="dramaOverview?.summary.unknown_schedule_count">未配置更新日 {{ dramaOverview.summary.unknown_schedule_count }}</span>
          <span v-else>更新日配置完整</span>
        </div>
      </div>
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">本月新增</div>
          <span class="metric-tile__icon metric-tile__icon--success" aria-hidden="true">
            <el-icon><CirclePlus /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ monthSuccessCount }}</div>
        <div class="metric-tile__hint">本月成功执行次数</div>
      </div>
      <div
        class="glass-panel metric-tile"
        :class="{ 'metric-tile--warn': !!capacityOverview?.warning_accounts.length }"
      >
        <div class="metric-tile__head">
          <div class="metric-tile__label">网盘空间使用率</div>
          <span
            class="metric-tile__icon"
            :class="capacityOverview?.warning_accounts.length ? 'metric-tile__icon--warn' : 'metric-tile__icon--cyan'"
            aria-hidden="true"
          >
            <el-icon><Odometer /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ capacityOverview ? formatPercent(capacityOverview.summary.usage_ratio) : '--' }}</div>
        <div class="metric-tile__hint">
          <span v-if="capacityOverview">
            {{ capacitySpaceText }} · 预警 {{ capacityOverview.warning_accounts.length }}
          </span>
          <span v-else>汇总已用 / 总容量</span>
        </div>
      </div>
      <div
        class="glass-panel metric-tile"
        :class="{ 'metric-tile--warn': !!dramaOverview?.summary.execution_failed }"
      >
        <div class="metric-tile__head">
          <div class="metric-tile__label">任务成功率</div>
          <span
            class="metric-tile__icon"
            :class="dramaOverview?.summary.execution_failed ? 'metric-tile__icon--warn' : 'metric-tile__icon--success'"
            aria-hidden="true"
          >
            <el-icon><CircleCheck /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ successRateText }}</div>
        <div class="metric-tile__hint">
          <span v-if="dramaOverview">
            成功 {{ dramaOverview.summary.execution_success }} · 失败 {{ dramaOverview.summary.execution_failed }}
          </span>
          <span v-else>暂无执行记录</span>
        </div>
      </div>
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">最新剧集比例</div>
          <span class="metric-tile__icon metric-tile__icon--violet" aria-hidden="true">
            <el-icon><Film /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ updateProgressText }}</div>
        <div class="metric-tile__hint">
          <span v-if="updateProgressSummary.linked">
            最新 {{ updateProgressSummary.latest }} · 落后 {{ updateProgressSummary.behind }} · 未知 {{ updateProgressSummary.unknown }}
          </span>
          <span v-else>仅统计已关联 TMDB 的任务</span>
        </div>
      </div>
    </section>

    <div class="dashboard-log-grid">
      <ShareLinkHistory
        v-loading="dramaLoading"
        :records="dramaOverview?.shareurl_update_logs ?? []"
        @cleared="onShareLogsCleared"
      />

      <DashboardLogList
        v-model:expanded="showAllFailures"
        v-loading="dramaLoading"
        title="近期失败"
        :records="failures"
        :record-key="record => `${record.task_id}-${record.started_at}-${record.stage}`"
        empty-text="暂无近期失败记录"
      >
        <template #actions>
          <el-button
            size="small"
            text
            type="danger"
            :loading="clearingFailures"
            :disabled="!failures.length"
            @click="clearFailures"
          >
            清理失败记录
          </el-button>
        </template>
        <template #status>
          <el-tag size="small" type="danger">失败</el-tag>
        </template>
        <template #meta="{ record }">
          <time :datetime="record.started_at">{{ formatDateTime(record.started_at) }}</time>
          <span v-if="record.stage">{{ failureStageLabel(record.stage) }}</span>
        </template>
        <template #default="{ record }">
          <div class="failure-entry-body">
            <FailureReason :message="record.message" />
            <router-link class="failure-task-link" :to="{ name: 'DramaTasks', query: { taskId: record.task_id } }">
              查看任务
            </router-link>
          </div>
        </template>
      </DashboardLogList>
    </div>

    <div class="updates-row">
      <section class="glass-panel updates-card" v-loading="dramaLoading">
        <div class="section-header">
          <div class="section-header__title">
            <div class="section-header__eyebrow">Updates</div>
            <h2>最近更新</h2>
            <div class="section-header__desc">
              <span>调度 {{ dramaOverview?.scheduler.enabled ? '已开启' : '未开启' }}</span>
              <span class="dot">·</span>
              <span>失败 {{ dramaOverview?.summary.execution_failed || 0 }}</span>
            </div>
          </div>
          <div class="toolbar__right">
            <router-link to="/tasks/drama">
              <el-button>追剧任务</el-button>
            </router-link>
            <router-link to="/tasks/calendar">
              <el-button>追剧日历</el-button>
            </router-link>
            <el-button @click="trendDialogVisible = true">任务趋势</el-button>
          </div>
        </div>

        <div v-if="!recentUpdates.length" class="empty-state empty-state--compact">
          <div class="empty-state__title">暂无转存记录</div>
          <div class="empty-state__desc">暂无新文件转存记录。</div>
        </div>
        <div v-else class="update-list">
          <div v-for="item in recentUpdates" :key="`${item.task_id}-${item.started_at}`" class="update-row">
            <div class="update-row__main">
              <div class="update-row__title">
                <span class="update-row__icon" aria-hidden="true">
                  <el-icon><VideoPlay /></el-icon>
                </span>
                <span class="update-row__name">{{ titleFromTask(item.task) }}</span>
              </div>
              <div class="update-row__desc">
                <span>{{ timeAgoFromIso(item.started_at) }}</span>
                <span class="meta-dot">·</span>
                <span class="update-row__drive">
                  <DriveTypeIcon v-if="item.drive_type" :type="item.drive_type" :size="15" />
                  <span>{{ DRIVE_TYPE_META[item.drive_type as DriveType]?.label || item.drive_type || item.task.account_name || '自动网盘' }}</span>
                </span>
                <span class="meta-dot">·</span>
                <el-tag size="small" type="success">成功</el-tag>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="glass-panel upcoming-card" v-loading="dramaLoading">
        <div class="section-header">
          <div class="section-header__title">
            <div class="section-header__eyebrow">Upcoming</div>
            <h2>即将播出</h2>
            <div class="section-header__desc">
              <span v-if="upcomingAiring.length">未来 7 天 {{ upcomingAiring.length }} 部</span>
              <span v-else-if="tmdbLoading">TMDB 资料加载中…</span>
              <span v-else>未来 7 天暂无</span>
            </div>
          </div>
        </div>

        <div v-if="!upcomingAiring.length" class="empty-state empty-state--compact">
          <div class="empty-state__title">暂无即将播出剧集</div>
          <div class="empty-state__desc">未来 7 天暂无已识别的更新日。</div>
        </div>
        <div v-else class="upcoming-list">
          <div v-for="it in displayedUpcomingAiring" :key="`${it.task.id}-${it.air_date}`" class="upcoming-row">
            <div class="upcoming-row__left">
              <span class="update-row__icon update-row__icon--calendar" aria-hidden="true">
                <el-icon><Calendar /></el-icon>
              </span>
              <span class="upcoming-row__name">{{ titleFromTask(it.task) }}</span>
              <template v-if="it.predicted">
                <span class="meta-dot">·</span>
                <span class="upcoming-row__predicted">预计更新</span>
              </template>
              <template v-else-if="it.episode">
                <span class="meta-dot">·</span>
                <span>{{ it.episode }}</span>
              </template>
            </div>
            <div class="upcoming-row__right">{{ relativeDayLabel(it.air_date) }}</div>
          </div>
        </div>
      </section>
    </div>

    <el-dialog v-model="trendDialogVisible" title="任务趋势（成功数 / 失败数 / 成功率）" width="920">
      <DramaTrend :points="trendPoints" />
    </el-dialog>

    <section class="glass-panel capacity-section">
      <div class="capacity-title">
        <div class="capacity-title__left">
          <div class="section-header__eyebrow">Capacity</div>
          <div class="capacity-title__name">容量管理</div>
        </div>
        <div class="capacity-title__right">
          <span class="status-pill" :class="capacityOverview?.warning_accounts.length ? 'status-pill--danger' : 'status-pill--success'">
            预警 {{ capacityOverview?.warning_accounts.length || 0 }}
          </span>
          <span class="status-pill">最近刷新 {{ formatDateTime(capacityOverview?.updated_at) }}</span>
          <router-link to="/extensions/drives">
            <el-button size="small">账号管理</el-button>
          </router-link>
          <el-button type="primary" size="small" :loading="capacityRefreshing" @click="handleRefreshCapacity">刷新容量</el-button>
        </div>
      </div>

      <div v-loading="capacityLoading" class="capacity-body">
        <CapacityHero v-if="capacityOverview" :summary="capacityOverview.summary" />

        <section class="glass-panel capacity-overview-card">
          <div class="section-header">
            <div class="section-header__title">
              <div class="section-header__eyebrow">Overview</div>
              <h2>总体占比</h2>
              <div class="section-header__desc">基于所有支持容量统计的账号进行汇总，不支持容量的账号会在下方单独列出。</div>
            </div>
            <div v-if="hasCapacityData && capacityOverview" class="capacity-overview-summary">
              <span class="capacity-overview-summary__ratio">{{ formatPercent(capacityOverview.summary.usage_ratio) }}</span>
              <span class="capacity-overview-summary__detail">{{ capacitySpaceText }}</span>
            </div>
          </div>

          <template v-if="hasCapacityData && capacityOverview">
            <el-progress
              class="capacity-overview-progress"
              :percentage="Math.round((capacityOverview.summary.usage_ratio || 0) * 100)"
              :stroke-width="12"
              :show-text="false"
              :status="(capacityOverview.summary.usage_ratio || 0) >= 0.85 ? 'exception' : 'success'"
            />
          </template>
          <div v-else class="empty-state empty-state--compact">
            <div class="empty-state__title">暂无可统计的容量数据</div>
            <div class="empty-state__desc">点击右上角“刷新容量”获取各网盘空间快照。</div>
          </div>
        </section>

        <section v-if="capacityOverview" class="glass-panel dashboard-section">
          <div class="group-stack__title">
            <h3>预警账号</h3>
            <span class="status-pill" :class="capacityOverview.warning_accounts.length ? 'status-pill--danger' : 'status-pill--success'">
              {{ capacityOverview.warning_accounts.length }} 个账号
            </span>
          </div>
          <div v-if="capacityOverview.warning_accounts.length" class="card-grid" style="margin-top: 18px">
            <CapacityAccountCard v-for="item in capacityOverview.warning_accounts" :key="item.id" :account="item" />
          </div>
          <div v-else class="empty-state empty-state--compact">
            <div class="empty-state__title">暂无容量预警</div>
            <div class="empty-state__desc">所有账号当前空间使用率均处于设定阈值之内。</div>
          </div>
        </section>

        <template v-if="hasAccounts">
          <section v-for="group in accountGroups" :key="group.name" class="group-stack">
            <div class="group-stack__title">
              <h3>{{ group.name }}</h3>
              <span class="status-pill">{{ group.accounts.length }} 个账号</span>
            </div>
            <div class="card-grid">
              <CapacityAccountCard v-for="item in group.accounts" :key="item.id" :account="item" />
            </div>
          </section>
        </template>

        <section v-if="capacityOverview?.unsupported_accounts.length" class="glass-panel dashboard-section">
          <div class="group-stack__title">
            <h3>未支持容量的账号</h3>
            <span class="status-pill">{{ capacityOverview.unsupported_accounts.length }} 个账号</span>
          </div>
          <div class="card-grid" style="margin-top: 18px">
            <CapacityAccountCard
              v-for="item in capacityOverview.unsupported_accounts"
              :key="`unsupported-${item.id}`"
              :account="item"
              compact
            />
          </div>
        </section>

        <section v-if="capacityOverview && !hasAccounts" class="glass-panel dashboard-section">
          <div class="empty-copy">当前还没有创建任何网盘账号，请先前往“账号管理”添加账号，再回到此处查看容量总览。</div>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard-metrics {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

@media (max-width: 1360px) {
  .dashboard-metrics { grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
}

@media (max-width: 560px) {
  .dashboard-metrics { grid-template-columns: minmax(0, 1fr); }
}

.update-row__drive {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.dot,
.meta-dot {
  margin: 0 6px;
  color: var(--el-text-color-secondary);
}

.updates-card {
  padding: 22px;
}

.updates-row {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 0.85fr);
  gap: 20px;
  align-items: stretch;
  min-width: 0;
}

.updates-row > * {
  min-width: 0;
}

.updates-card,
.upcoming-card {
  min-width: 0;
}

.update-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
}

.update-row {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

@media (hover: hover) and (pointer: fine) {
  .update-row:hover,
  .upcoming-row:hover {
    border-color: var(--line-strong);
    background: var(--el-fill-color-lighter);
  }
}

.update-row__title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
}

.update-row__icon {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 14px;
}

.update-row__icon--calendar {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.update-row__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}

.update-row__desc {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.dashboard-log-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr));
  gap: 20px;
  align-items: start;
}

.failure-entry-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.failure-task-link {
  align-self: flex-end;
  padding-block: 3px;
  color: var(--el-color-primary);
  font-size: 12px;
  white-space: nowrap;
}

.failure-task-link:hover {
  text-decoration: underline;
}

.upcoming-card {
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.upcoming-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upcoming-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  padding: 14px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.upcoming-row__left {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 650;
  overflow: hidden;
}

.upcoming-row__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 360px;
}

.upcoming-row__predicted {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.upcoming-row__right {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.capacity-section {
  padding: 12px 16px;
  content-visibility: auto;
  contain-intrinsic-size: auto none auto 320px;
}

.group-stack {
  content-visibility: auto;
  contain-intrinsic-size: auto none auto 320px;
}

.capacity-overview-summary {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.capacity-overview-summary__ratio {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}

.capacity-overview-summary__detail {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.capacity-overview-progress {
  margin-top: 16px;
}

.capacity-title {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.capacity-title__left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.capacity-title__name {
  font-size: 16px;
  font-weight: 700;
}

.capacity-title__right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.capacity-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0 10px;
}

@media (max-width: 1100px) {
  .dashboard-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .updates-row {
    grid-template-columns: 1fr;
  }

  .update-row__name {
    max-width: 260px;
  }

  .upcoming-row__name {
    max-width: 220px;
  }
}
</style>
