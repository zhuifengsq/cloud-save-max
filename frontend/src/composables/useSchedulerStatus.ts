import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fetchTaskSchedulerStatus } from '@/api/tasks'
import { useAuthStore } from '@/stores/auth'
import { markTasksChanged } from '@/utils/taskChangeBus'

const IDLE_POLL_MS = 20_000
const RUNNING_POLL_MS = 5_000

/**
 * 格式化定时任务执行中状态的悬停提示文案，按已运行时长给出简洁反馈。
 */
export function formatSchedulerRunningTooltip(startedAt?: string | null, nowMs = Date.now()): string {
  if (!startedAt) return '定时任务执行中'
  const parsed = Date.parse(startedAt)
  if (!Number.isFinite(parsed)) return '定时任务执行中'
  const diffMs = Math.max(0, nowMs - parsed)
  const totalMinutes = Math.floor(diffMs / 60_000)
  if (totalMinutes < 1) return '定时任务执行中'
  if (totalMinutes < 60) return `定时任务执行中 · 已运行 ${totalMinutes} 分钟`
  const hours = Math.floor(totalMinutes / 60)
  const mins = totalMinutes % 60
  return mins > 0
    ? `定时任务执行中 · 已运行 ${hours} 小时 ${mins} 分钟`
    : `定时任务执行中 · 已运行 ${hours} 小时`
}

/**
 * 轻量监听后台每日定时追剧任务是否在执行，并在结束瞬间触发页面静默刷新。
 */
export function useSchedulerStatus() {
  const auth = useAuthStore()
  const running = ref(false)
  const startedAt = ref<string | null>(null)
  const nowMs = ref(Date.now())

  const tooltip = computed(() =>
    running.value ? formatSchedulerRunningTooltip(startedAt.value, nowMs.value) : '',
  )

  let timer: number | null = null
  let unmounted = false
  let inFlight = false
  let pollSeq = 0

  function clearTimer() {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function scheduleNext() {
    clearTimer()
    if (unmounted || document.hidden || !auth.isAuthenticated) return
    const delay = running.value ? RUNNING_POLL_MS : IDLE_POLL_MS
    timer = window.setTimeout(() => {
      void poll()
    }, delay)
  }

  async function poll() {
    if (unmounted || document.hidden || !auth.isAuthenticated || inFlight) return
    inFlight = true
    const currentSeq = ++pollSeq
    try {
      const res = await fetchTaskSchedulerStatus()
      if (unmounted || currentSeq !== pollSeq) return
      const wasRunning = running.value
      const nextRunning = Boolean(res?.running)
      running.value = nextRunning
      startedAt.value = nextRunning ? (res?.started_at ?? null) : null
      nowMs.value = Date.now()
      if (wasRunning && !nextRunning) {
        markTasksChanged()
      }
    } catch {
      // 忽略瞬时网络波动，保持原有状态并按周期继续探测
    } finally {
      inFlight = false
      scheduleNext()
    }
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      clearTimer()
      return
    }
    if (auth.isAuthenticated) {
      clearTimer()
      void poll()
    }
  }

  watch(
    () => auth.isAuthenticated,
    (authed) => {
      if (!authed) {
        pollSeq += 1
        clearTimer()
        running.value = false
        startedAt.value = null
        return
      }
      if (!document.hidden) {
        clearTimer()
        void poll()
      }
    },
  )

  onMounted(() => {
    document.addEventListener('visibilitychange', handleVisibilityChange)
    if (!document.hidden && auth.isAuthenticated) {
      void poll()
    }
  })

  onBeforeUnmount(() => {
    unmounted = true
    pollSeq += 1
    clearTimer()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })

  return {
    running,
    startedAt,
    tooltip,
  }
}
