import { reactive } from 'vue'

import { fetchTMDBDetail } from '@/api/media'
import type { TMDBDetail } from '@/types/media'

// TMDB 剧集资料的跨页面共享缓存：工作台和追剧日历共用，
// 页面切换时只要还在有效期内就直接复用，不再请求。
export const TMDB_DETAIL_REFRESH_MS = 30 * 60 * 1000
// 拉取失败后短暂退避，避免每分钟的静默刷新反复打同一个失败的剧集；
// 手动“刷新追剧数据”会带 force，绕过退避立即重试。
const FAILURE_RETRY_MS = 5 * 60 * 1000

export const tmdbDetailsById = reactive<Record<number, TMDBDetail | undefined>>({})

const fetchedAt: Record<number, number> = {}
const versions: Record<number, number> = {}
const failureAt: Record<number, number> = {}
const inFlight = new Map<number, Promise<TMDBDetail | null>>()
// 全局变更计数：任何一次成功拉取/失效都会 +1，页面据此判断派生数据要不要重算
let storeVersion = 0

export function getTmdbDetailStoreVersion() {
  return storeVersion
}

export type TmdbDetailEntry = {
  detail: TMDBDetail
  at: number
  version: number
}

// 只缓存成功结果；失败不写缓存，下次调用会重新尝试（除非仍在退避时间内）。
export function getTmdbDetailEntry(id: number): TmdbDetailEntry | undefined {
  const detail = tmdbDetailsById[id]
  if (!detail) return undefined
  return { detail, at: fetchedAt[id] || 0, version: versions[id] || 0 }
}

export function isTmdbDetailStale(id: number, maxAgeMs = TMDB_DETAIL_REFRESH_MS) {
  if (!tmdbDetailsById[id]) return true
  return Date.now() - (fetchedAt[id] || 0) > maxAgeMs
}

export function tmdbDetailIdsNeedingRefresh(ids: number[], maxAgeMs = TMDB_DETAIL_REFRESH_MS) {
  return ids.filter((id) => isTmdbDetailStale(id, maxAgeMs))
}

export async function ensureTmdbDetail(
  tmdbId: number,
  opts: { force?: boolean; maxAgeMs?: number } = {},
): Promise<TMDBDetail | null> {
  const id = Number(tmdbId) || 0
  if (id <= 0) return null

  const existingRequest = inFlight.get(id)
  if (existingRequest) return existingRequest

  const force = opts.force === true
  const maxAgeMs = opts.maxAgeMs ?? TMDB_DETAIL_REFRESH_MS
  if (!force) {
    if (tmdbDetailsById[id] && Date.now() - (fetchedAt[id] || 0) <= maxAgeMs) return tmdbDetailsById[id] ?? null
    const failedAt = failureAt[id]
    if (failedAt && Date.now() - failedAt < FAILURE_RETRY_MS) return tmdbDetailsById[id] ?? null
  }

  const version = versions[id] || 0
  const request = (async () => {
    try {
      const detail = await fetchTMDBDetail('tv', id, force ? { force: true } : undefined)
      if ((versions[id] || 0) !== version) return tmdbDetailsById[id] ?? null
      tmdbDetailsById[id] = detail
      fetchedAt[id] = Date.now()
      versions[id] = version + 1
      storeVersion += 1
      delete failureAt[id]
      return detail
    } catch {
      // 失败时保留上一次成功的资料，调用方继续展示旧内容
      if ((versions[id] || 0) === version) failureAt[id] = Date.now()
      return tmdbDetailsById[id] ?? null
    }
  })()
  inFlight.set(id, request)
  try {
    return await request
  } finally {
    if (inFlight.get(id) === request) inFlight.delete(id)
  }
}

async function runWithConcurrency<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  const queue = [...items]
  const runners: Promise<void>[] = []
  const runOne = async () => {
    while (queue.length) {
      const item = queue.shift()
      if (item === undefined) return
      await worker(item)
    }
  }
  for (let i = 0; i < Math.max(1, limit); i += 1) {
    runners.push(runOne())
  }
  await Promise.all(runners)
}

export async function ensureTmdbDetails(
  ids: number[],
  opts: { force?: boolean; maxAgeMs?: number; concurrency?: number } = {},
): Promise<void> {
  const unique = Array.from(new Set(ids.map((id) => Number(id) || 0).filter((id) => id > 0)))
  if (!unique.length) return
  await runWithConcurrency(unique, opts.concurrency ?? 4, async (id) => {
    await ensureTmdbDetail(id, opts)
  })
}

export function invalidateTmdbDetails(ids?: number[]) {
  const targets = ids?.length
    ? ids.map((id) => Number(id) || 0)
    : new Set([...Object.keys(tmdbDetailsById).map(Number), ...inFlight.keys()])
  for (const id of targets) {
    if (id <= 0) continue
    versions[id] = (versions[id] || 0) + 1
    inFlight.delete(id)
    delete tmdbDetailsById[id]
    delete fetchedAt[id]
    delete failureAt[id]
  }
  storeVersion += 1
}
