import { ref } from 'vue'
import { fetchTaskOverview, fetchTaskPage, fetchTaskOrderIndex } from '@/api/tasks'
import type { TaskOverview, TaskPage, TaskPageQuery, TaskReadFilters, TaskOrderIndexItem } from '@/types/tasks'
import { getTasksVersion, subscribeTasksChanged } from '@/utils/taskChangeBus'
import { orderedTaskIds } from '@/utils/taskReadOrder'

export const sharedTasks = ref<TaskOverview[]>([])
let loadedAt = 0
let loadedOnce = false
let inFlight: Promise<TaskOverview[]> | null = null
const pages = new Map<string, Promise<TaskPage>>()
const indexes = new Map<string, Promise<TaskOrderIndexItem[]>>()
export function sharedTasksLoadedAt() { return loadedAt }
export function hasSharedTasks() { return loadedOnce }
export async function loadSharedTasks(opts: {maxAgeMs?:number} = {}): Promise<TaskOverview[]> {
  if (loadedOnce && Date.now() - loadedAt < (opts.maxAgeMs || 0)) return sharedTasks.value
  if (inFlight) return inFlight
  const version = getTasksVersion()
  const request = fetchTaskOverview().then(all => {
    if (version === getTasksVersion()) {
      sharedTasks.value = all; loadedAt = Date.now(); loadedOnce = true
    }
    return all
  })
  inFlight = request
  try { return await request } finally { if (inFlight === request) inFlight = null }
}
export function dropSharedTasks() {
  sharedTasks.value = []; loadedAt = 0; loadedOnce = false; inFlight = null
  pages.clear(); indexes.clear()
}
subscribeTasksChanged(dropSharedTasks)
function key(value: object) { return JSON.stringify(Object.entries(value).sort(([a],[b]) => a.localeCompare(b))) }
export function loadTaskOrderIndex(filters: TaskReadFilters) {
  const k = key(filters)
  let request = indexes.get(k)
  if (!request) {
    request = fetchTaskOrderIndex(filters).catch(error => { if (indexes.get(k) === request) indexes.delete(k); throw error })
    indexes.set(k, request)
  }
  return request
}
export function loadTaskPage(query: TaskPageQuery): Promise<TaskPage> {
  const k = key(query)
  const existing = pages.get(k)
  if (existing) return existing
  const request = (async () => {
    const {name_order, ...params} = query
    if (!name_order) return fetchTaskPage(params)
    const {page, page_size, ...filters} = params
    const index = await loadTaskOrderIndex(filters)
    const lastPage = Math.max(1, Math.ceil(index.length / page_size))
    const selectedPage = Math.min(page, lastPage)
    const ids = orderedTaskIds(index, name_order, selectedPage, page_size)
    return fetchTaskPage({...params, page:selectedPage, ...(ids.length ? {ids:ids.join(',')} : {})})
  })().catch(error => { if (pages.get(k) === request) pages.delete(k); throw error })
  pages.set(k, request)
  return request
}
