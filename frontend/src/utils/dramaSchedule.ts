// 追剧排期提取：以 TMDB seasons_full 的逐集 air_date 为准，next/last_episode_to_air 只作补充分支。
// 追剧日历会在裁剪 seasons_full 前调用 getDramaSchedule 预热，工作台随后仍能读取这份排期。
import { tvTotalEpisodesFromDetail } from '@/utils/dramaEpisodeProgress'

export type WeekdayKey = 1 | 2 | 3 | 4 | 5 | 6 | 7

export interface DramaEpisodeRef {
  season: number
  episode: number
}

export interface DramaSchedule {
  /** 去重并升序的播出日 */
  episodeDates: Date[]
  /** 'YYYY-MM-DD' -> 当天播出的集（可能同一天多集） */
  episodesByDate: Record<string, DramaEpisodeRef[]>
  inferredWeekdays: WeekdayKey[]
  inferredCycleDays: number | null
  totalEpisodes: number | null
}

export interface UpcomingAiring {
  air_date: string
  episode: string
  /** true 表示按更新星期推断出来的日期，TMDB 尚未给出确切集号 */
  predicted: boolean
  episode_refs: DramaEpisodeRef[]
}

const scheduleCache = new WeakMap<object, DramaSchedule>()
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/

export function parseDateOnly(value: unknown): Date | null {
  const s = String(value ?? '').trim()
  if (!DATE_ONLY_RE.test(s)) return null
  const [y, m, d] = s.split('-').map((x) => Number(x))
  const dt = new Date(y, (m || 1) - 1, d || 1)
  return Number.isNaN(dt.getTime()) ? null : dt
}

export function formatDateKey(d: Date): string {
  const y = String(d.getFullYear())
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function dateOnly(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

export function addDays(d: Date, delta: number): Date {
  const out = dateOnly(d)
  out.setDate(out.getDate() + delta)
  return out
}

function diffDays(a: Date, b: Date): number {
  const aa = dateOnly(a).getTime()
  const bb = dateOnly(b).getTime()
  return Math.round((aa - bb) / 86400000)
}

export function isoWeekdayFromDate(d: Date): WeekdayKey {
  const js = d.getDay()
  return (((js + 6) % 7) + 1) as WeekdayKey
}

export function normalizeWeekdays(value: unknown): WeekdayKey[] {
  const arr = Array.isArray(value) ? value : []
  const days = arr.map((x) => Number(x)).filter((x) => x >= 1 && x <= 7) as WeekdayKey[]
  return Array.from(new Set(days)).sort((a, b) => a - b) as WeekdayKey[]
}

export function inferWeekdaysFromEpisodeDates(dates: Date[]): WeekdayKey[] {
  if (!dates.length) return []
  const counts = new Map<WeekdayKey, number>()
  for (const d of dates) {
    const wd = isoWeekdayFromDate(d)
    counts.set(wd, (counts.get(wd) || 0) + 1)
  }
  const unique = Array.from(counts.keys()).sort((a, b) => a - b) as WeekdayKey[]
  const total = dates.length
  const sorted = Array.from(counts.entries()).sort((a, b) => b[1] - a[1])
  const [mostDay, mostCount] = sorted[0]
  let picked: WeekdayKey[] = []
  if (total >= 4 && mostCount / total >= 0.6) {
    picked = [mostDay]
  } else {
    for (const [d, c] of sorted) {
      if (c >= 2 && c / total >= 0.2) picked.push(d)
    }
    if (!picked.length) picked = sorted.map(([d]) => d)
  }
  picked = picked.sort((a, b) => a - b) as WeekdayKey[]
  if (total >= 7 && unique.length >= 5 && mostCount / total <= 0.4) return unique
  return picked.slice(0, 3) as WeekdayKey[]
}

export function inferCycleDaysFromEpisodeDates(dates: Date[]): number | null {
  if (dates.length < 3) return null
  const sorted = dates.slice().sort((a, b) => a.getTime() - b.getTime())
  const deltas: number[] = []
  for (let i = 1; i < sorted.length; i += 1) {
    const d = diffDays(sorted[i], sorted[i - 1])
    if (d >= 1 && d <= 30) deltas.push(d)
  }
  if (!deltas.length) return null
  const freq = new Map<number, number>()
  for (const d of deltas) freq.set(d, (freq.get(d) || 0) + 1)
  const ranked = Array.from(freq.entries()).sort((a, b) => b[1] - a[1])
  const [mode, modeCount] = ranked[0]
  if (modeCount >= Math.max(3, Math.ceil(deltas.length * 0.3))) return mode
  const copy = deltas.slice().sort((a, b) => a - b)
  return copy[Math.floor(copy.length / 2)]
}

function nextDateByWeekdays(after: Date, days: WeekdayKey[]): Date {
  const start = addDays(after, 1)
  for (let i = 0; i < 14; i += 1) {
    const d = addDays(start, i)
    if (days.includes(isoWeekdayFromDate(d))) return d
  }
  return addDays(start, 7)
}

function buildDramaSchedule(data: any): DramaSchedule {
  const byDate = new Map<string, DramaEpisodeRef[]>()
  const addDate = (value: unknown, season: number, episode: number) => {
    const dt = parseDateOnly(value)
    if (!dt) return
    const key = formatDateKey(dt)
    const list = byDate.get(key) || []
    if (season > 0 && episode > 0 && !list.some((r) => r.season === season && r.episode === episode)) {
      list.push({ season, episode })
      list.sort((a, b) => a.season - b.season || a.episode - b.episode)
    }
    byDate.set(key, list)
  }

  const seasonsFull = Array.isArray(data?.seasons_full) ? data.seasons_full : []
  for (const s of seasonsFull) {
    if (!s || typeof s !== 'object') continue
    const seasonNumber = Number(s.season_number) || 0
    if (seasonNumber <= 0) continue
    const episodes = Array.isArray(s.episodes) ? s.episodes : []
    for (const ep of episodes) {
      if (!ep || typeof ep !== 'object') continue
      addDate(ep.air_date, seasonNumber, Number(ep.episode_number) || 0)
    }
  }
  const last = data?.last_episode_to_air
  if (last && typeof last === 'object') {
    addDate(last.air_date, Number(last.season_number) || 0, Number(last.episode_number) || 0)
  }
  const next = data?.next_episode_to_air
  if (next && typeof next === 'object') {
    addDate(next.air_date, Number(next.season_number) || 0, Number(next.episode_number) || 0)
  }

  const episodeDates = Array.from(byDate.keys())
    .map((key) => parseDateOnly(key))
    .filter((d): d is Date => !!d)
    .sort((a, b) => a.getTime() - b.getTime())

  const episodesByDate: Record<string, DramaEpisodeRef[]> = {}
  for (const [key, refs] of byDate) {
    episodesByDate[key] = refs
  }

  return {
    episodeDates,
    episodesByDate,
    inferredWeekdays: inferWeekdaysFromEpisodeDates(episodeDates),
    inferredCycleDays: inferCycleDaysFromEpisodeDates(episodeDates),
    totalEpisodes: tvTotalEpisodesFromDetail(data),
  }
}

export function getDramaSchedule(data: any): DramaSchedule | null {
  if (!data || typeof data !== 'object') return null
  const cached = scheduleCache.get(data)
  if (cached) return cached
  const schedule = buildDramaSchedule(data)
  scheduleCache.set(data, schedule)
  return schedule
}

function countKnownEpisodes(schedule: DramaSchedule): number {
  let count = 0
  for (const refs of Object.values(schedule.episodesByDate)) count += refs.length
  return count
}

function episodesPerDateAverage(schedule: DramaSchedule): number {
  const keys = Object.keys(schedule.episodesByDate).sort()
  if (!keys.length) return 1
  const recent = keys.slice(-7)
  const total = recent.reduce((sum, key) => sum + (schedule.episodesByDate[key]?.length || 0), 0)
  return Math.max(1, Math.round(total / recent.length))
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function episodeCode(ref: DramaEpisodeRef): string {
  return `S${pad2(ref.season)}E${pad2(ref.episode)}`
}

export function formatEpisodeLabel(refs: DramaEpisodeRef[]): string {
  const valid = refs
    .filter((r) => r.season > 0 && r.episode > 0)
    .slice()
    .sort((a, b) => a.season - b.season || a.episode - b.episode)
  if (!valid.length) return ''
  if (valid.length === 1) return episodeCode(valid[0])
  const sameSeason = valid.every((r) => r.season === valid[0].season)
  if (sameSeason) {
    const eps = valid.map((r) => r.episode).sort((a, b) => a - b)
    let contiguous = true
    for (let i = 1; i < eps.length; i += 1) {
      if (eps[i] !== eps[i - 1] + 1) contiguous = false
    }
    if (contiguous && eps.length <= 4) {
      return `S${pad2(valid[0].season)}E${pad2(eps[0])}-${pad2(eps[eps.length - 1])}`
    }
  }
  return valid.map(episodeCode).join('、')
}

/**
 * 找一部剧在 [fromStr, toStr] 窗口内最近的一次播出（含预测）。
 * updateWeekdays 来自 TMDBDetail.update_weekdays，优先于按历史日期推断的星期。
 */
export function findUpcomingAiring(
  data: any,
  updateWeekdays: unknown,
  fromStr: string,
  toStr: string,
): UpcomingAiring | null {
  if (!data || typeof data !== 'object') return null
  const from = parseDateOnly(fromStr)
  const to = parseDateOnly(toStr)
  if (!from || !to || from.getTime() > to.getTime()) return null
  const schedule = getDramaSchedule(data)
  if (!schedule) return null

  // 1) seasons_full 里的确切日期优先
  for (const d of schedule.episodeDates) {
    if (d.getTime() < from.getTime()) continue
    if (d.getTime() > to.getTime()) break
    const key = formatDateKey(d)
    const refs = schedule.episodesByDate[key] || []
    return { air_date: key, episode: formatEpisodeLabel(refs), predicted: false, episode_refs: refs }
  }

  // 2) 没有确切日期时，按更新星期预测最近一次
  const status = String(data?.status || '').trim().toLowerCase()
  if (status === 'ended' || status === 'canceled') return null
  const total = schedule.totalEpisodes
  const knownEpisodes = countKnownEpisodes(schedule)
  if (total != null && knownEpisodes >= total) return null

  const tmdbWeekdays = normalizeWeekdays(updateWeekdays)
  const days = tmdbWeekdays.length ? tmdbWeekdays : schedule.inferredWeekdays
  if (!days.length) return null

  const lastKnown = schedule.episodeDates.length
    ? schedule.episodeDates[schedule.episodeDates.length - 1]
    : null
  const start = lastKnown || addDays(from, -1)
  if (start.getTime() >= to.getTime()) return null

  const perDay = episodesPerDateAverage(schedule)
  const cycleDays = days.length === 1 ? schedule.inferredCycleDays : null
  let remaining = total != null ? Math.max(0, total - knownEpisodes) : null
  let cursor = dateOnly(start)
  for (let i = 0; i < 400; i += 1) {
    cursor = cycleDays && days.length === 1 ? addDays(cursor, cycleDays) : nextDateByWeekdays(cursor, days)
    if (cursor.getTime() > to.getTime()) return null
    if (remaining != null && remaining <= 0) return null
    if (cursor.getTime() >= from.getTime()) {
      return { air_date: formatDateKey(cursor), episode: '', predicted: true, episode_refs: [] }
    }
    if (remaining != null) remaining -= perDay
  }
  return null
}
