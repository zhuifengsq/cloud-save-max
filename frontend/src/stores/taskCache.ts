import { ref } from 'vue'

import { fetchTasks } from '@/api/tasks'
import type { TaskItem } from '@/types/tasks'

// 任务列表的跨页面共享缓存：工作台和追剧日历共用同一份数据，
// 来回切换页面不会重复请求；各页面自己决定什么时候刷新。
export const sharedTasks = ref<TaskItem[]>([])

let loadedAt = 0
let loadedOnce = false
let inFlight: Promise<TaskItem[]> | null = null

export function sharedTasksLoadedAt() {
  return loadedAt
}

export function hasSharedTasks() {
  return loadedOnce
}

// 同一时间只允许一个任务列表请求在飞，其它调用复用同一个 Promise。
export async function loadSharedTasks(opts: { maxAgeMs?: number } = {}): Promise<TaskItem[]> {
  const maxAge = Math.max(0, Number(opts.maxAgeMs) || 0)
  if (loadedOnce && maxAge > 0 && Date.now() - loadedAt < maxAge) return sharedTasks.value
  if (inFlight) return inFlight
  const request = (async () => {
    const all = await fetchTasks()
    sharedTasks.value = all
    loadedAt = Date.now()
    loadedOnce = true
    return all
  })()
  inFlight = request
  try {
    return await request
  } finally {
    if (inFlight === request) inFlight = null
  }
}

export function dropSharedTasks() {
  sharedTasks.value = []
  loadedAt = 0
  loadedOnce = false
  inFlight = null
}
