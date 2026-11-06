import { http } from '@/api/http'
import type { DoubanCategoryList, MediaDiscoverList, TMDBCredits, TMDBDetail, TMDBSearchList } from '@/types/media'

const TMDB_DETAIL_TTL_MS = 60_000
const TMDB_CREDITS_TTL_MS = 5 * 60_000

type TMDBDetailCacheEntry = {
  data: TMDBDetail
  expiresAt: number
}

const tmdbDetailCache = new Map<string, TMDBDetailCacheEntry>()
const tmdbDetailInFlight = new Map<string, Promise<TMDBDetail>>()

type TMDBCreditsCacheEntry = {
  data: TMDBCredits
  expiresAt: number
}

const tmdbCreditsCache = new Map<string, TMDBCreditsCacheEntry>()
const tmdbCreditsInFlight = new Map<string, Promise<TMDBCredits>>()

function cacheKey(mediaType: 'movie' | 'tv', id: number) {
  return `${mediaType}:${id}`
}

function cloneTMDBDetail(data: TMDBDetail): TMDBDetail {
  const anyGlobal: any = globalThis as any
  if (typeof anyGlobal?.structuredClone === 'function') return anyGlobal.structuredClone(data)
  return JSON.parse(JSON.stringify(data))
}

export function invalidateTMDBDetailCache(mediaType?: 'movie' | 'tv', id?: number) {
  if (!mediaType || !id) {
    tmdbDetailCache.clear()
    tmdbDetailInFlight.clear()
    return
  }
  const key = cacheKey(mediaType, id)
  tmdbDetailCache.delete(key)
  tmdbDetailInFlight.delete(key)
}

export async function fetchDoubanCategories() {
  const response = await http.get<DoubanCategoryList>('/media/douban/categories')
  return response.data
}

export async function fetchDoubanList(params: { main_category: string; sub_category?: string; start?: number; limit?: number }) {
  const response = await http.get<MediaDiscoverList>('/media/douban/list', { params })
  return response.data
}

export async function searchTMDB(params: { q: string; type?: 'multi' | 'movie' | 'tv'; page?: number; year?: string }) {
  const response = await http.get<TMDBSearchList>('/media/search', { params })
  return response.data
}

export async function fetchTMDBDetail(mediaType: 'movie' | 'tv', id: number, opts?: { force?: boolean }) {
  const mid = Number(id) || 0
  if (mid <= 0) {
    const response = await http.get<TMDBDetail>(`/media/${mediaType}/${id}`)
    return response.data
  }

  const key = cacheKey(mediaType, mid)
  const now = Date.now()

  if (!opts?.force) {
    const cached = tmdbDetailCache.get(key)
    if (cached && cached.expiresAt > now) return cloneTMDBDetail(cached.data)
    const inflight = tmdbDetailInFlight.get(key)
    if (inflight) return cloneTMDBDetail(await inflight)
  }

  const p = (async () => {
    const response = await http.get<TMDBDetail>(`/media/${mediaType}/${mid}`)
    return response.data
  })()

  tmdbDetailInFlight.set(key, p)
  try {
    const data = await p
    if (tmdbDetailInFlight.get(key) === p) {
      tmdbDetailCache.set(key, { data, expiresAt: now + TMDB_DETAIL_TTL_MS })
    }
    return cloneTMDBDetail(data)
  } finally {
    if (tmdbDetailInFlight.get(key) === p) tmdbDetailInFlight.delete(key)
  }
}

export async function fetchTMDBCredits(
  mediaType: 'movie' | 'tv',
  id: number,
  opts?: { force?: boolean; limit?: number },
) {
  const mid = Number(id) || 0
  const limit = Number(opts?.limit) > 0 ? Math.min(30, Math.max(1, Math.floor(Number(opts?.limit)))) : 0
  const params = limit ? { limit } : undefined
  if (mid <= 0) {
    const response = await http.get<TMDBCredits>(`/media/${mediaType}/${id}/credits`, { params })
    return response.data
  }

  const key = `${mediaType}:credits:${mid}:${limit}`
  const now = Date.now()

  if (!opts?.force) {
    const cached = tmdbCreditsCache.get(key)
    if (cached && cached.expiresAt > now) return cached.data
    const inflight = tmdbCreditsInFlight.get(key)
    if (inflight) return await inflight
  }

  const p = (async () => {
    const response = await http.get<TMDBCredits>(`/media/${mediaType}/${mid}/credits`, { params })
    return response.data
  })()

  tmdbCreditsInFlight.set(key, p)
  try {
    const data = await p
    tmdbCreditsCache.set(key, { data, expiresAt: Date.now() + TMDB_CREDITS_TTL_MS })
    return data
  } finally {
    tmdbCreditsInFlight.delete(key)
  }
}
