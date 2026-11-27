<script setup lang="ts">
import { ArrowRight, Calendar, CircleCheck, CirclePlus, Collection, Film, Odometer, Refresh, VideoPlay } from '@element-plus/icons-vue'
import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'
import { DRIVE_TYPE_META, type DriveType } from '@/utils/driveType'
import { ElMessage, ElMessageBox } from 'element-plus'

import CapacityAccountCard from '@/components/dashboard/CapacityAccountCard.vue'
import CapacityHero from '@/components/dashboard/CapacityHero.vue'
import DashboardLogList from '@/components/dashboard/DashboardLogList.vue'
import DramaTrend from '@/components/dashboard/DramaTrend.vue'
import ShareLinkHistory from '@/components/dashboard/ShareLinkHistory.vue'
import FailureReason from '@/components/dashboard/FailureReason.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { failureStageLabel } from '@/utils/failureMessage'
import { clearDramaFailures, fetchCapacityOverview, fetchDramaOverview, fetchDramaUpdates } from '@/api/dashboard'
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
import type { CapacityOverview, DramaOverview, DramaUpdateGroup, DramaUpdatesRange, DramaUpdatesResponse } from '@/types/dashboard'
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
const showDramaSkeleton = computed(() => dramaLoading.value && !dramaOverview.value)
const showCapacitySkeleton = computed(() => capacityLoading.value && !capacityOverview.value)

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

const isMobile = useIsMobile()
const updatePosterFailed = reactive<Record<string, boolean>>({})

function posterUrlByTmdbId(tmdbId?: number | null) {
  const id = Number(tmdbId || 0)
  if (!id) return ''
  const path = String(tmdbDetailsById[id]?.data?.poster_path || '').trim()
  if (!path) return ''
  const url = `https://image.tmdb.org/t/p/w185${path}`
  if (updatePosterFailed[url]) return ''
  return url
}

function onPosterErrorByTmdbId(tmdbId?: number | null) {
  const id = Number(tmdbId || 0)
  if (!id) return
  const path = String(tmdbDetailsById[id]?.data?.poster_path || '').trim()
  if (!path) return
  updatePosterFailed[`https://image.tmdb.org/t/p/w185${path}`] = true
}

function updatePosterUrl(task: TaskOverview) {
  return posterUrlByTmdbId(task.tmdb_id)
}

function onUpdatePosterError(task: TaskOverview) {
  onPosterErrorByTmdbId(task.tmdb_id)
}

function updateTransferCount(item: { transfer_count?: number; new_files?: string[] }) {
  const listLen = Array.isArray(item.new_files) ? item.new_files.length : 0
  return Math.max(0, Number(item.transfer_count) || 0, listLen)
}

function updateFilesSummary(item: { transfer_count?: number; new_files?: string[] }) {
  const files = Array.isArray(item.new_files) ? item.new_files.filter(Boolean) : []
  const count = updateTransferCount(item)
  if (files.length) {
    return count > 1 ? `${files[0]} 等 ${count} 项` : files[0]
  }
  if (count > 0) return `新存入 ${count} 项`
  return '已转存新文件'
}

function formatShortAirDate(dateStr?: string | null) {
  const raw = String(dateStr || '').trim()
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw.slice(5, 10)
  return raw
}

const UPDATES_RANGE_OPTIONS: { label: string; value: DramaUpdatesRange }[] = [
  { label: '今天', value: 'today' },
  { label: '近 24 小时', value: '24h' },
  { label: '近 7 天', value: '7d' },
  { label: '近 30 天', value: '30d' },
]

const UPDATES_RANGE_TITLES: Record<DramaUpdatesRange, string> = {
  today: '今天（00:00 至今）',
  '24h': '近 24 小时',
  '7d': '近 7 天',
  '30d': '近 30 天',
}

const updatesDialog = reactive({
  visible: false,
  loading: false,
  error: '',
  range: 'today' as DramaUpdatesRange,
  data: null as DramaUpdatesResponse | null,
})
let updatesDialogVersion = 0

const updatesDialogSubtitle = computed(() => {
  const rangeTitle = UPDATES_RANGE_TITLES[updatesDialog.range]
  const data = updatesDialog.data
  if (!data || updatesDialog.loading && !data) return rangeTitle
  if (data.drama_count <= 0) return `${rangeTitle} · 暂无新存入`
  return `${rangeTitle} · 共 ${data.drama_count} 部剧 · ${data.drive_update_count} 个网盘 · 新增 ${data.total_transfer_count} 项`
})

const updatesDialogEmptyText = computed(() => `${UPDATES_RANGE_TITLES[updatesDialog.range]}暂无新文件存入`)

function openUpdatesDialog() {
  updatesDialog.range = 'today'
  updatesDialog.visible = true
  void loadDramaUpdates()
}

async function loadDramaUpdates() {
  const version = ++updatesDialogVersion
  updatesDialog.loading = true
  updatesDialog.error = ''
  try {
    const result = await fetchDramaUpdates(updatesDialog.range)
    if (version !== updatesDialogVersion) return
    updatesDialog.data = result
  } catch {
    if (version === updatesDialogVersion) updatesDialog.error = '更新明细加载失败'
  } finally {
    if (version === updatesDialogVersion) updatesDialog.loading = false
  }
}

function groupTitle(group: DramaUpdateGroup) {
  const tmdbId = Number(group.tmdb_id || 0)
  if (tmdbId > 0) {
    const detail = tmdbDetailsById[tmdbId]?.data
    const title = String(detail?.title || detail?.name || '').trim()
    if (title) return title
  }
  return group.title
}

function formatUpdatesTime(value?: string | null, range: DramaUpdatesRange = 'today') {
  const raw = String(value || '').trim()
  if (!raw) return '—'
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (range === 'today') return hm
  const now = new Date()
  if (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  ) {
    return `今天 ${hm}`
  }
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${hm}`
}

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
        <el-segmented
          v-model="dramaDays"
          :options="[
            { label: '近7天', value: 7 },
            { label: '近30天', value: 30 },
          ]"
        />
        <el-button type="primary" :loading="dramaRefreshing" @click="handleRefreshDrama">刷新追剧数据</el-button>
      </div>
    </div>

    <section v-if="showDramaSkeleton" class="metric-strip dashboard-metrics" aria-hidden="true">
      <div v-for="i in 5" :key="`metric-sk-${i}`" class="glass-panel metric-tile metric-tile--skeleton">
        <div class="metric-tile__head">
          <span class="ui-skeleton" style="width: 68px; height: 14px"></span>
          <span class="ui-skeleton ui-skeleton--circle" style="width: 28px; height: 28px"></span>
        </div>
        <span class="ui-skeleton" style="width: 72px; height: 30px; margin-top: 12px"></span>
        <span class="ui-skeleton" style="width: 112px; height: 12px; margin-top: 10px"></span>
      </div>
    </section>
    <section v-else class="metric-strip dashboard-metrics" v-loading="dramaLoading && Boolean(dramaOverview)">
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

    <div v-if="showDramaSkeleton" class="dashboard-log-grid" aria-hidden="true">
      <article v-for="i in 2" :key="`log-sk-${i}`" class="glass-panel dashboard-log-skeleton">
        <div class="dashboard-log-skeleton__head">
          <span class="ui-skeleton" style="width: 108px; height: 20px"></span>
          <span class="ui-skeleton" style="width: 64px; height: 16px"></span>
        </div>
        <div v-for="j in 2" :key="`log-entry-sk-${i}-${j}`" class="dashboard-log-skeleton__entry">
          <div class="dashboard-log-skeleton__row">
            <span class="ui-skeleton" style="width: 48%; height: 16px"></span>
            <span class="ui-skeleton ui-skeleton--pill" style="width: 52px; height: 20px"></span>
          </div>
          <span class="ui-skeleton" style="width: 66%; height: 12px"></span>
          <span class="ui-skeleton" style="width: 88%; height: 14px"></span>
        </div>
      </article>
    </div>
    <div v-else class="dashboard-log-grid">
      <ShareLinkHistory
        v-loading="dramaLoading && Boolean(dramaOverview)"
        :records="dramaOverview?.shareurl_update_logs ?? []"
        @cleared="onShareLogsCleared"
      />

      <DashboardLogList
        v-model:expanded="showAllFailures"
        v-loading="dramaLoading && Boolean(dramaOverview)"
        title="近期失败"
        :records="failures"
        :record-key="record => `${record.task_id}-${record.started_at}-${record.stage}`"
        empty-text="暂无近期失败记录"
      >
        <template #actions>
          <el-button
            v-if="failures.length"
            size="small"
            text
            type="danger"
            :loading="clearingFailures"
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
      <section class="glass-panel updates-card" v-loading="dramaLoading && !showDramaSkeleton">
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
              <el-button text>追剧任务</el-button>
            </router-link>
            <router-link to="/tasks/calendar">
              <el-button text>追剧日历</el-button>
            </router-link>
            <el-button @click="trendDialogVisible = true">任务趋势</el-button>
          </div>
        </div>

        <div v-if="showDramaSkeleton" class="update-list" aria-hidden="true">
          <div v-for="i in 4" :key="`upd-sk-${i}`" class="update-row">
            <span class="ui-skeleton" style="width: 36px; height: 50px; border-radius: 8px; flex: none"></span>
            <div style="flex: 1; display: grid; gap: 8px; min-width: 0">
              <span class="ui-skeleton" style="width: 48%; height: 15px"></span>
              <span class="ui-skeleton" style="width: 68%; height: 12px"></span>
            </div>
            <div style="display: grid; justify-items: end; gap: 6px; flex: none">
              <span class="ui-skeleton" style="width: 64px; height: 20px; border-radius: 999px"></span>
              <span class="ui-skeleton" style="width: 46px; height: 12px"></span>
            </div>
          </div>
        </div>
        <div v-else-if="!recentUpdates.length" class="empty-state empty-state--compact">
          <div class="empty-state__title">暂无转存记录</div>
          <div class="empty-state__desc">暂无新文件转存记录。</div>
        </div>
        <div v-else class="update-list">
          <router-link
            v-for="item in recentUpdates"
            :key="`${item.task_id}-${item.started_at}`"
            :to="{ path: '/tasks/drama', query: { taskId: String(item.task_id) } }"
            class="update-row update-row--link"
          >
            <img
              v-if="updatePosterUrl(item.task)"
              :src="updatePosterUrl(item.task)"
              :alt="titleFromTask(item.task)"
              class="update-row__poster"
              loading="lazy"
              @error="onUpdatePosterError(item.task)"
            />
            <span v-else class="update-row__poster-fallback" aria-hidden="true">
              <el-icon><VideoPlay /></el-icon>
            </span>

            <div class="update-row__main">
              <div class="update-row__title">
                <span class="update-row__name">{{ titleFromTask(item.task) }}</span>
                <span class="update-row__drive">
                  <DriveTypeIcon v-if="item.drive_type" :type="item.drive_type" :size="14" />
                  <span>{{ DRIVE_TYPE_META[item.drive_type as DriveType]?.label || item.drive_type || item.task.account_name || '自动网盘' }}</span>
                </span>
              </div>
              <div class="update-row__files" :title="updateFilesSummary(item)">
                {{ updateFilesSummary(item) }}
              </div>
            </div>

            <div class="update-row__aside">
              <span v-if="updateTransferCount(item) > 0" class="update-row__count">
                +{{ updateTransferCount(item) }} 新存入
              </span>
              <span class="update-row__time">{{ timeAgoFromIso(item.started_at) }}</span>
            </div>
          </router-link>
        </div>
        <div v-if="!showDramaSkeleton" class="updates-card__footer">
          <el-button text type="primary" size="small" class="updates-card__more-btn" @click="openUpdatesDialog">
            <span>查看更多更新</span>
            <el-icon class="el-icon--right"><ArrowRight /></el-icon>
          </el-button>
        </div>
      </section>

      <section class="glass-panel upcoming-card" v-loading="dramaLoading && !showDramaSkeleton">
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

        <div v-if="showDramaSkeleton" class="upcoming-list" aria-hidden="true">
          <div v-for="i in 4" :key="`upc-sk-${i}`" class="upcoming-row">
            <span class="ui-skeleton" style="width: 36px; height: 50px; border-radius: 8px; flex: none"></span>
            <div style="flex: 1; display: grid; gap: 8px; min-width: 0">
              <span class="ui-skeleton" style="width: 56%; height: 15px"></span>
              <span class="ui-skeleton" style="width: 34%; height: 12px"></span>
            </div>
            <div style="display: grid; justify-items: end; gap: 6px; flex: none">
              <span class="ui-skeleton" style="width: 48px; height: 20px; border-radius: 999px"></span>
              <span class="ui-skeleton" style="width: 40px; height: 12px"></span>
            </div>
          </div>
        </div>
        <div v-else-if="!upcomingAiring.length" class="empty-state empty-state--compact">
          <div class="empty-state__title">暂无即将播出剧集</div>
          <div class="empty-state__desc">未来 7 天暂无已识别的更新日。</div>
        </div>
        <div v-else class="upcoming-list">
          <router-link
            v-for="it in displayedUpcomingAiring"
            :key="`${it.task.id}-${it.air_date}`"
            :to="{ path: '/tasks/drama', query: { taskId: String(it.task.id) } }"
            class="upcoming-row upcoming-row--link"
          >
            <img
              v-if="updatePosterUrl(it.task)"
              :src="updatePosterUrl(it.task)"
              :alt="titleFromTask(it.task)"
              class="update-row__poster"
              loading="lazy"
              @error="onUpdatePosterError(it.task)"
            />
            <span v-else class="update-row__poster-fallback update-row__poster-fallback--calendar" aria-hidden="true">
              <el-icon><Calendar /></el-icon>
            </span>

            <div class="upcoming-row__main">
              <el-tooltip :content="titleFromTask(it.task)" placement="top" :trigger="['hover', 'focus']" :show-after="250">
                <span class="upcoming-row__name" tabindex="0">{{ titleFromTask(it.task) }}</span>
              </el-tooltip>
              <div class="upcoming-row__sub">
                <span v-if="it.predicted" class="upcoming-row__predicted">预计更新</span>
                <span v-else-if="it.episode" class="upcoming-row__ep">{{ it.episode }}</span>
                <span v-else class="upcoming-row__predicted">即将更新</span>
              </div>
            </div>

            <div class="upcoming-row__aside">
              <span class="upcoming-row__day" :class="{ 'is-today': relativeDayLabel(it.air_date) === '今天' }">
                {{ relativeDayLabel(it.air_date) }}
              </span>
              <span v-if="formatShortAirDate(it.air_date)" class="upcoming-row__date">
                {{ formatShortAirDate(it.air_date) }}
              </span>
            </div>
          </router-link>
        </div>
      </section>
    </div>

    <el-dialog v-model="trendDialogVisible" title="任务趋势（成功数 / 失败数 / 成功率）" width="920">
      <DramaTrend :points="trendPoints" bare />
    </el-dialog>

    <el-dialog
      v-model="updatesDialog.visible"
      :width="isMobile ? '94vw' : '700px'"
      class="updates-dialog"
    >
      <template #header>
        <div class="updates-dialog__header">
          <div class="updates-dialog__heading">
            <h3 class="updates-dialog__title">更新明细</h3>
            <p class="updates-dialog__subtitle">{{ updatesDialogSubtitle }}</p>
          </div>
          <el-button
            class="updates-dialog__refresh"
            size="small"
            :icon="Refresh"
            :loading="updatesDialog.loading"
            plain
            @click="loadDramaUpdates"
          >
            刷新
          </el-button>
        </div>
      </template>

      <div class="updates-dialog__toolbar">
        <el-segmented
          v-model="updatesDialog.range"
          :options="UPDATES_RANGE_OPTIONS"
          size="small"
          :disabled="updatesDialog.loading"
          @change="loadDramaUpdates"
        />
      </div>

      <el-alert
        v-if="updatesDialog.error"
        :title="updatesDialog.error"
        type="error"
        :closable="false"
        class="updates-dialog__alert"
      >
        <el-button size="small" @click="loadDramaUpdates">重试</el-button>
      </el-alert>

      <div v-if="updatesDialog.loading && !updatesDialog.data" class="updates-group-list" aria-hidden="true">
        <div v-for="i in 3" :key="`upd-dlg-sk-${i}`" class="updates-group updates-group--skeleton">
          <div class="updates-group__head">
            <span class="ui-skeleton" style="width: 36px; height: 50px; border-radius: 8px; flex: none"></span>
            <div style="flex: 1; display: grid; gap: 6px">
              <span class="ui-skeleton" style="width: 42%; height: 15px"></span>
              <span class="ui-skeleton" style="width: 28%; height: 12px"></span>
            </div>
          </div>
          <div class="updates-group__drives">
            <span class="ui-skeleton" style="width: 100%; height: 48px; border-radius: 10px"></span>
          </div>
        </div>
      </div>

      <div v-else-if="!updatesDialog.error && !(updatesDialog.data?.groups.length)" class="updates-dialog__empty">
        <p>{{ updatesDialogEmptyText }}</p>
        <div v-if="updatesDialog.range === 'today'" class="updates-dialog__empty-actions">
          <el-button size="small" plain @click="updatesDialog.range = '7d'; loadDramaUpdates()">查看近 7 天</el-button>
          <el-button size="small" plain @click="updatesDialog.range = '30d'; loadDramaUpdates()">查看近 30 天</el-button>
        </div>
      </div>

      <div
        v-else-if="updatesDialog.data?.groups.length"
        class="updates-group-list"
        v-loading="updatesDialog.loading"
      >
        <article v-for="group in updatesDialog.data.groups" :key="group.drama_key" class="updates-group">
          <header class="updates-group__head">
            <img
              v-if="posterUrlByTmdbId(group.tmdb_id)"
              :src="posterUrlByTmdbId(group.tmdb_id)"
              :alt="groupTitle(group)"
              class="update-row__poster"
              loading="lazy"
              @error="onPosterErrorByTmdbId(group.tmdb_id)"
            />
            <span v-else class="update-row__poster-fallback" aria-hidden="true">
              <el-icon><VideoPlay /></el-icon>
            </span>

            <div class="updates-group__info">
              <div class="updates-group__title">{{ groupTitle(group) }}</div>
              <div class="updates-group__meta">
                <span>{{ group.drives.length }} 个网盘有更新</span>
                <span class="meta-dot">·</span>
                <span>最近 {{ formatUpdatesTime(group.latest_at, updatesDialog.range) }}</span>
              </div>
            </div>

            <span v-if="group.total_transfer_count > 0" class="update-row__count">
              +{{ group.total_transfer_count }} 新存入
            </span>
          </header>

          <div class="updates-group__drives">
            <router-link
              v-for="drive in group.drives"
              :key="drive.task_id"
              :to="{ path: '/tasks/drama', query: { taskId: String(drive.task_id) } }"
              class="updates-drive-row"
              @click="updatesDialog.visible = false"
            >
              <div class="updates-drive-row__top">
                <span class="updates-drive-row__brand">
                  <DriveTypeIcon v-if="drive.drive_type" :type="drive.drive_type" :size="15" />
                  <span class="updates-drive-row__label">
                    {{ DRIVE_TYPE_META[drive.drive_type as DriveType]?.label || drive.drive_type || '网盘任务' }}
                  </span>
                  <span v-if="drive.account_name" class="updates-drive-row__account">{{ drive.account_name }}</span>
                </span>

                <span class="updates-drive-row__stats">
                  <span class="updates-drive-row__time">{{ formatUpdatesTime(drive.started_at, updatesDialog.range) }}</span>
                  <span v-if="drive.transfer_count > 0" class="updates-drive-row__badge">+{{ drive.transfer_count }} 项</span>
                  <span class="updates-drive-row__go" aria-hidden="true">
                    <span>查看任务</span>
                    <ArrowRight class="updates-drive-row__arrow" />
                  </span>
                </span>
              </div>

              <div v-if="drive.new_files.length" class="updates-drive-row__files">
                <span
                  v-for="(fileName, index) in drive.new_files.slice(0, 3)"
                  :key="`${drive.task_id}-f-${index}`"
                  class="updates-drive-row__chip"
                  :title="fileName"
                >
                  {{ fileName }}
                </span>
                <span v-if="drive.transfer_count > Math.min(3, drive.new_files.length)" class="updates-drive-row__more">
                  等共 {{ drive.transfer_count }} 项
                </span>
              </div>
              <div v-else class="updates-drive-row__fallback">
                本次新存入 {{ drive.transfer_count }} 项
              </div>
            </router-link>
          </div>
        </article>
      </div>
    </el-dialog>

    <section class="capacity-section">
      <div class="capacity-title">
        <div class="capacity-title__left">
          <div class="section-header__eyebrow">Capacity</div>
          <div class="capacity-title__row">
            <h2>容量管理</h2>
            <span class="capacity-title__meta">
              <span :class="{ 'is-warn': Boolean(capacityOverview?.warning_accounts.length) }">
                预警 {{ capacityOverview?.warning_accounts.length || 0 }}
              </span>
              <span class="meta-dot">·</span>
              <span>最近刷新 {{ formatDateTime(capacityOverview?.updated_at) }}</span>
            </span>
          </div>
        </div>
        <div class="capacity-title__right">
          <router-link to="/extensions/drives">
            <el-button>账号管理</el-button>
          </router-link>
          <el-button type="primary" :loading="capacityRefreshing" @click="handleRefreshCapacity">刷新容量</el-button>
        </div>
      </div>

      <div v-if="showCapacitySkeleton" class="capacity-body capacity-skeleton" aria-hidden="true">
        <div class="metric-strip capacity-skeleton__metrics">
          <div v-for="i in 4" :key="`cap-hero-sk-${i}`" class="glass-panel metric-tile">
            <span class="ui-skeleton" style="width: 64px; height: 13px"></span>
            <span class="ui-skeleton" style="width: 88px; height: 28px; margin-top: 10px"></span>
            <span class="ui-skeleton" style="width: 110px; height: 12px; margin-top: 8px"></span>
          </div>
        </div>
        <div class="glass-panel capacity-overview-card">
          <span class="ui-skeleton" style="width: 120px; height: 18px"></span>
          <span class="ui-skeleton ui-skeleton--pill" style="width: 100%; height: 10px; margin-top: 12px"></span>
        </div>
        <div class="capacity-account-grid">
          <div v-for="i in 2" :key="`cap-card-sk-${i}`" class="glass-panel dashboard-log-skeleton__entry">
            <span class="ui-skeleton" style="width: 52%; height: 16px"></span>
            <span class="ui-skeleton" style="width: 78%; height: 12px"></span>
            <span class="ui-skeleton ui-skeleton--pill" style="width: 100%; height: 8px"></span>
          </div>
        </div>
      </div>

      <div v-else v-loading="capacityLoading && Boolean(capacityOverview)" class="capacity-body">
        <CapacityHero v-if="capacityOverview" :summary="capacityOverview.summary" />

        <section class="glass-panel capacity-overview-card">
          <template v-if="hasCapacityData && capacityOverview">
            <div class="capacity-overview-head">
              <h3>总体空间占比</h3>
              <div class="capacity-overview-summary">
                <span class="capacity-overview-summary__detail">{{ capacitySpaceText }}</span>
                <span class="capacity-overview-summary__ratio">{{ formatPercent(capacityOverview.summary.usage_ratio) }}</span>
              </div>
            </div>
            <el-progress
              class="capacity-overview-progress"
              :percentage="Math.round((capacityOverview.summary.usage_ratio || 0) * 100)"
              :stroke-width="10"
              :show-text="false"
              :status="(capacityOverview.summary.usage_ratio || 0) >= 0.85 ? 'exception' : 'success'"
            />
          </template>
          <div v-else class="empty-state empty-state--compact">
            <div class="empty-state__title">暂无可统计的容量数据</div>
            <div class="empty-state__desc">点击右上角“刷新容量”获取各网盘空间快照。</div>
          </div>
        </section>

        <section v-if="capacityOverview?.warning_accounts.length" class="group-stack">
          <div class="group-stack__title">
            <h3>预警账号</h3>
            <span class="status-pill status-pill--danger">
              {{ capacityOverview.warning_accounts.length }} 个账号
            </span>
          </div>
          <div class="capacity-account-grid">
            <CapacityAccountCard v-for="item in capacityOverview.warning_accounts" :key="item.id" :account="item" />
          </div>
        </section>

        <section v-if="capacityOverview?.accounts.length" class="group-stack">
          <div class="group-stack__title">
            <h3>网盘账号</h3>
            <span class="status-pill">{{ capacityOverview.accounts.length }} 个账号</span>
          </div>
          <div class="capacity-account-grid">
            <CapacityAccountCard v-for="item in capacityOverview.accounts" :key="item.id" :account="item" />
          </div>
        </section>

        <section v-if="capacityOverview?.unsupported_accounts.length" class="group-stack">
          <div class="group-stack__title">
            <h3>未支持容量的账号</h3>
            <span class="status-pill">{{ capacityOverview.unsupported_accounts.length }} 个账号</span>
          </div>
          <div class="capacity-account-grid">
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
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 804px;
  overflow: hidden;
}

.updates-card .section-header,
.upcoming-card .section-header {
  flex: none;
}

.update-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding-right: 2px;
}

.update-row {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  transition: transform 0.16s ease, border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

.update-row--link,
.upcoming-row--link {
  color: inherit;
  text-decoration: none;
  cursor: pointer;
}

.update-row--link:focus-visible,
.upcoming-row--link:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

@media (hover: hover) and (pointer: fine) {
  .update-row:hover,
  .upcoming-row:hover {
    border-color: var(--line-strong);
    background: var(--el-fill-color-lighter);
  }

  .update-row--link:hover,
  .upcoming-row--link:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 16px -12px rgba(15, 23, 42, 0.25);
  }
}

.update-row__poster,
.update-row__poster-fallback {
  flex: none;
  width: 36px;
  height: 50px;
  border-radius: 8px;
}

.update-row__poster {
  display: block;
  object-fit: cover;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--surface-muted);
}

.update-row__poster-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 16px;
}

.update-row__poster-fallback--calendar {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.update-row__main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 5px;
}

.update-row__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-weight: 700;
}

.update-row__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14.5px;
}

.update-row__drive {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.update-row__files {
  font-size: 12.5px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-row__aside {
  flex: none;
  display: grid;
  justify-items: end;
  gap: 5px;
  text-align: right;
}

.update-row__count {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--el-color-success) 34%, transparent);
  background: color-mix(in srgb, var(--el-color-success) 13%, transparent);
  color: var(--el-color-success);
  font-size: 11.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.update-row__time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.dashboard-log-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr));
  gap: 20px;
  align-items: stretch;
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

.upcoming-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding-right: 2px;
}

.upcoming-row {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  transition: transform 0.16s ease, border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

.updates-card .empty-state,
.upcoming-card .empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.upcoming-row__main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 5px;
}

.upcoming-row__name {
  min-width: 0;
  font-size: 14.5px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upcoming-row__sub {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}

.upcoming-row__ep {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.upcoming-row__predicted {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.upcoming-row__aside {
  flex: none;
  display: grid;
  justify-items: end;
  gap: 5px;
  text-align: right;
}

.upcoming-row__day {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--surface-muted);
  color: var(--el-text-color-secondary);
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}

.upcoming-row__day.is-today {
  border-color: color-mix(in srgb, var(--el-color-success) 34%, transparent);
  background: color-mix(in srgb, var(--el-color-success) 13%, transparent);
  color: var(--el-color-success);
}

.upcoming-row__date {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.capacity-section {
  padding: 16px 0 0;
  border-top: 1px solid var(--line-soft);
  display: flex;
  flex-direction: column;
  gap: 18px;
  content-visibility: auto;
  contain-intrinsic-size: auto none auto 320px;
}

.group-stack {
  content-visibility: auto;
  contain-intrinsic-size: auto none auto 320px;
}

.capacity-overview-card {
  padding: 18px 22px;
}

.capacity-overview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.capacity-overview-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}

.capacity-overview-summary {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.capacity-overview-summary__ratio {
  font-size: 20px;
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
  margin-top: 12px;
}

.capacity-title {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 14px;
  flex-wrap: wrap;
}

.capacity-title__left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.capacity-title__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.capacity-title__row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.capacity-title__meta {
  font-size: 12.5px;
  color: var(--el-text-color-secondary);
}

.capacity-title__meta .is-warn {
  color: var(--el-color-danger);
  font-weight: 600;
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
  gap: 18px;
  padding: 0;
}

.capacity-account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 380px), 1fr));
  gap: 18px;
}

@media (max-width: 1100px) {
  .dashboard-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .updates-row {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .updates-card,
  .upcoming-card {
    max-height: none;
    overflow: visible;
  }

  .update-list,
  .upcoming-list {
    flex: none;
    overflow-y: visible;
    padding-right: 0;
  }

  .updates-card .empty-state,
  .upcoming-card .empty-state {
    flex: none;
  }
}

@media (max-width: 767px) {
  .dashboard-log-grid {
    align-items: start;
  }
  .dashboard-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }

  .dashboard-metrics .metric-tile {
    padding: 14px 12px;
  }

  .dashboard-metrics .metric-tile:last-child:nth-child(odd) {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 4px 12px;
  }

  .dashboard-metrics .metric-tile:last-child:nth-child(odd) .metric-tile__head {
    grid-column: 1;
    grid-row: 1;
    justify-content: flex-start;
  }

  .dashboard-metrics .metric-tile:last-child:nth-child(odd) .metric-tile__hint {
    grid-column: 1;
    grid-row: 2;
    margin-top: 0;
  }

  .dashboard-metrics .metric-tile:last-child:nth-child(odd) .metric-tile__value {
    grid-column: 2;
    grid-row: 1 / span 2;
    margin-top: 0;
    font-size: 26px;
  }

  .dashboard-metrics .metric-tile__label {
    font-size: 12px;
    white-space: nowrap;
  }

  .dashboard-metrics .metric-tile__icon {
    width: 24px;
    height: 24px;
    font-size: 13px;
  }

  .dashboard-metrics .metric-tile__value {
    margin-top: 6px;
    font-size: 24px;
  }

  .dashboard-metrics .metric-tile__hint {
    margin-top: 6px;
    font-size: 11.5px;
  }
}

.dashboard-log-skeleton {
  padding: 22px;
  display: grid;
  gap: 14px;
}

.dashboard-log-skeleton__head,
.dashboard-log-skeleton__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dashboard-log-skeleton__entry {
  display: grid;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  background: var(--el-bg-color);
}

.capacity-skeleton__metrics {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

@media (max-width: 1024px) {
  .capacity-skeleton__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .capacity-skeleton__metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}

.updates-card__footer {
  flex: none;
  display: flex;
  justify-content: center;
  padding-top: 4px;
  border-top: 1px solid var(--el-border-color-extra-light);
}

.updates-card__more-btn {
  font-weight: 500;
}

.updates-dialog__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-right: 26px;
}

.updates-dialog__heading {
  min-width: 0;
}

.updates-dialog__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--ink);
}

.updates-dialog__subtitle {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.45;
  color: var(--ink-muted);
  overflow-wrap: anywhere;
}

.updates-dialog__refresh {
  flex: none;
}

.updates-dialog__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  overflow-x: auto;
}

.updates-dialog__alert {
  margin-bottom: 12px;
}

.updates-dialog__empty {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 12px;
  min-height: 170px;
  padding: 20px;
  border: 1px dashed var(--line);
  border-radius: 14px;
  background: var(--surface-muted);
  color: var(--ink-muted);
  font-size: 13px;
  text-align: center;
}

.updates-dialog__empty p {
  margin: 0;
}

.updates-dialog__empty-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

.updates-group-list {
  display: grid;
  gap: 12px;
  max-height: 58vh;
  overflow-y: auto;
  padding: 2px;
}

.updates-group {
  display: grid;
  gap: 10px;
  padding: 14px;
  border-radius: 14px;
  border: 1px solid var(--line);
  background: var(--surface-muted);
}

.updates-group__head {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.updates-group__info {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}

.updates-group__title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.updates-group__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  font-size: 12px;
  color: var(--ink-muted);
}

.updates-group__drives {
  display: grid;
  gap: 8px;
}

.updates-drive-row {
  display: grid;
  gap: 7px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  text-decoration: none;
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.updates-drive-row:hover {
  transform: translateY(-1px);
  border-color: rgba(53, 102, 232, 0.36);
  box-shadow: 0 6px 16px -12px rgba(15, 23, 42, 0.24);
}

.updates-drive-row:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.updates-drive-row__top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.updates-drive-row__brand {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.updates-drive-row__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.updates-drive-row__account {
  font-size: 12px;
  color: var(--ink-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}

.updates-drive-row__stats {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.updates-drive-row__time {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-muted);
}

.updates-drive-row__badge {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--el-color-success) 34%, transparent);
  background: color-mix(in srgb, var(--el-color-success) 13%, transparent);
  color: var(--el-color-success);
  font-size: 11.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.updates-drive-row__go {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 12px;
  font-weight: 500;
  color: var(--brand);
}

.updates-drive-row__arrow {
  width: 12px;
  height: 12px;
}

.updates-drive-row__files {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.updates-drive-row__chip {
  display: inline-block;
  max-width: 100%;
  padding: 2px 8px;
  border-radius: 7px;
  border: 1px solid var(--line);
  background: var(--surface-muted);
  color: var(--ink);
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.updates-drive-row__more,
.updates-drive-row__fallback {
  font-size: 12px;
  color: var(--ink-muted);
}

@media (max-width: 767px) {
  .updates-drive-row__top {
    flex-direction: column;
    align-items: flex-start;
  }

  .updates-drive-row__stats {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
