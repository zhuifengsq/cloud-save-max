<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'

import { fetchTMDBDetail, invalidateTMDBDetailCache } from '@/api/media'
import { fetchTasks } from '@/api/tasks'
import type { TMDBDetail } from '@/types/media'
import type { TaskItem } from '@/types/tasks'
import { DRIVE_TYPE_META, detectDriveTypeByUrl, type DriveType } from '@/utils/driveType'

type WeekdayKey = 1 | 2 | 3 | 4 | 5 | 6 | 7

type CalendarEntry = {
  weekday: WeekdayKey
  taskId: number
  tmdbId: number | null
  task: TaskItem
  title: string
  posterUrl: string
  progressText: string
  progressPercent: number | null
  predictedAiredTotal: number | null
  predictedPercent: number | null
  totalEpisodes: number | null
  nextAirDate: string | null
  confirmed: boolean
  dateKey: string
  episodeNumbers: number[]
  predictedEpisodes: number | null
  progressItems: Array<{ key: string; rank: number; label: string; percent: number | null; text: string }>
}

const weekdays: Array<{ key: WeekdayKey; label: string }> = [
  { key: 1, label: '周一' },
  { key: 2, label: '周二' },
  { key: 3, label: '周三' },
  { key: 4, label: '周四' },
  { key: 5, label: '周五' },
  { key: 6, label: '周六' },
  { key: 7, label: '周日' },
]

const TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'

const posterFailures = reactive<Record<string, 'small' | 'unavailable'>>({})
function handlePosterError(url: string) {
  posterFailures[url] = posterFailures[url] === 'small' ? 'unavailable' : 'small'
}
function posterSource(url: string) {
  return posterFailures[url] === 'small' ? url.replace('/w500/', '/w185/') : url
}

const loading = ref(false)
const tasks = ref<TaskItem[]>([])
const detailsById = reactive<Record<number, TMDBDetail | undefined>>({})
const detailPromises = reactive<Record<number, Promise<TMDBDetail | null> | undefined>>({})
const episodeDatesById = reactive<Record<number, Date[] | undefined>>({})
const episodeNumbersById = reactive<Record<number, Record<string, number[]> | undefined>>({})
const inferredWeekdaysById = reactive<Record<number, WeekdayKey[] | undefined>>({})
const inferredCycleDaysById = reactive<Record<number, number | null | undefined>>({})
const viewMode = ref<'week' | 'month'>('week')
const monthCursor = ref(new Date(new Date().getFullYear(), new Date().getMonth(), 1))
const viewport = reactive({ width: window.innerWidth })
const isMobile = computed(() => viewport.width <= 768)
const selectedDateKey = ref<string>('')

function normalizeWeekdays(value: any): WeekdayKey[] {
  const arr = Array.isArray(value) ? value : []
  const days = arr.map((x) => Number(x)).filter((x) => x >= 1 && x <= 7) as WeekdayKey[]
  return Array.from(new Set(days)).sort((a, b) => a - b) as WeekdayKey[]
}

function addDays(d: Date, delta: number) {
  const out = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  out.setDate(out.getDate() + delta)
  return out
}

function diffDays(a: Date, b: Date) {
  const aa = new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime()
  const bb = new Date(b.getFullYear(), b.getMonth(), b.getDate()).getTime()
  return Math.round((aa - bb) / 86400000)
}

async function runWithConcurrency<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  const queue = [...items]
  const runners: Promise<void>[] = []
  const runOne = async () => {
    while (queue.length) {
      const it = queue.shift()
      if (it === undefined) return
      await worker(it)
    }
  }
  for (let i = 0; i < Math.max(1, limit); i += 1) {
    runners.push(runOne())
  }
  await Promise.all(runners)
}

function tvTotalEpisodesFromDetail(detail: any) {
  const n = detail?.number_of_episodes
  if (typeof n === 'number' && n > 0) return n
  const seasons = Array.isArray(detail?.seasons) ? detail.seasons : []
  const sum = seasons
    .filter((s: any) => s && typeof s === 'object' && Number(s.season_number) > 0)
    .reduce((acc: number, s: any) => acc + (Number(s.episode_count) || 0), 0)
  return sum > 0 ? sum : null
}

function tvAiredEpisodesFromDetail(detail: any, total: number | null) {
  const status = String(detail?.status || '').toLowerCase()
  if (status === 'ended' && typeof total === 'number' && total > 0) return total
  const last = detail?.last_episode_to_air
  if (!last || typeof last !== 'object') return null
  const seasonNumber = Number(last.season_number) || 0
  const episodeNumber = Number(last.episode_number) || 0
  if (seasonNumber <= 0 || episodeNumber <= 0) return null

  const seasons = Array.isArray(detail?.seasons) ? detail.seasons : []
  const prev = seasons
    .filter((s: any) => s && typeof s === 'object' && Number(s.season_number) > 0 && Number(s.season_number) < seasonNumber)
    .reduce((acc: number, s: any) => acc + (Number(s.episode_count) || 0), 0)
  const aired = prev + episodeNumber
  return aired > 0 ? aired : null
}

function tvProgressTextFromDetail(detail: any) {
  const seasons = typeof detail?.number_of_seasons === 'number' ? detail.number_of_seasons : null
  const total = tvTotalEpisodesFromDetail(detail)
  const aired = tvAiredEpisodesFromDetail(detail, total)
  const last = detail?.last_episode_to_air
  const lastSeason = Number(last?.season_number) || 0
  const lastEp = Number(last?.episode_number) || 0

  const parts: string[] = []
  if (seasons != null) parts.push(`季数：${seasons}`)
  if (total != null) parts.push(`总集数：${total}`)
  if (aired != null && total != null) parts.push(`已播：${aired}/${total}`)
  else if (aired != null) parts.push(`已播：${aired}`)
  if (lastSeason > 0 && lastEp > 0) parts.push(`当前到 S${lastSeason}E${lastEp}`)
  return parts.join(' · ')
}

function tvProgressBriefFromDetail(detail: any) {
  const total = tvTotalEpisodesFromDetail(detail)
  const aired = tvAiredEpisodesFromDetail(detail, total)
  if (aired != null && total != null) return `${aired} / ${total}`
  if (aired != null) return `${aired}`
  if (total != null) return `? / ${total}`
  return ''
}

function tvProgressPercentFromDetail(detail: any) {
  const total = tvTotalEpisodesFromDetail(detail)
  const aired = tvAiredEpisodesFromDetail(detail, total)
  if (aired == null || total == null) return null
  const t = Number(total) || 0
  const a = Number(aired) || 0
  if (t <= 0) return null
  const pct = Math.floor((a / t) * 100)
  return Math.max(0, Math.min(100, pct))
}

function tvAiredTotalFromDetail(detail: any) {
  const total = tvTotalEpisodesFromDetail(detail)
  const aired = tvAiredEpisodesFromDetail(detail, total)
  if (aired == null) return null
  const a = Number(aired) || 0
  return a > 0 ? a : null
}

function displayTitle(task: TaskItem, detail: TMDBDetail | null) {
  const data = detail?.data || {}
  const name = String(data?.name || data?.title || '').trim()
  if (name) return name
  return String(task.taskname || '').trim() || `任务 #${task.id}`
}

function nextAirDateFromDetail(detail: TMDBDetail | null) {
  const d = detail?.data || {}
  const air = String(d?.next_episode_to_air?.air_date || '').trim()
  return air || null
}

function progressFromDetail(task: TaskItem, detail: TMDBDetail | null) {
  const mt = String(task.tmdb_media_type || '').toLowerCase()
  if (mt !== 'tv') return ''
  const data = detail?.data || null
  if (!data) return ''
  return tvProgressTextFromDetail(data)
}

function posterUrlFromDetail(detail: TMDBDetail | null) {
  const data = detail?.data || {}
  const p = String(data?.poster_path || '').trim()
  if (!p) return ''
  return `${TMDB_IMAGE_BASE}${p}`
}

function isoWeekdayFromDate(d: Date): WeekdayKey {
  const js = d.getDay()
  return (((js + 6) % 7) + 1) as WeekdayKey
}

function inferWeekdaysFromEpisodeDates(dates: Date[]): WeekdayKey[] {
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

function inferCycleDaysFromEpisodeDates(dates: Date[]): number | null {
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

function extractEpisodeDatesFromDetailData(data: any): Date[] {
  const out: Date[] = []
  const seasonsFull = Array.isArray(data?.seasons_full) ? data.seasons_full : []
  for (const s of seasonsFull) {
    if (!s || typeof s !== 'object') continue
    const sn = Number(s?.season_number) || 0
    if (sn <= 0) continue
    const eps = Array.isArray(s?.episodes) ? s.episodes : []
    for (const ep of eps) {
      if (!ep || typeof ep !== 'object') continue
      const ad = String(ep?.air_date || '').trim()
      const dt = ad ? parseDateOnly(ad) : null
      if (dt) out.push(dt)
    }
  }
  const hasSameDate = (dt: Date) => out.some((x) => dateOnly(x).getTime() === dateOnly(dt).getTime())
  const last = String(data?.last_episode_to_air?.air_date || '').trim()
  const lastDt = last ? parseDateOnly(last) : null
  if (lastDt && !hasSameDate(lastDt)) out.push(lastDt)
  const next = String(data?.next_episode_to_air?.air_date || '').trim()
  const nextDt = next ? parseDateOnly(next) : null
  if (nextDt && !hasSameDate(nextDt)) out.push(nextDt)
  out.sort((a, b) => a.getTime() - b.getTime())
  return out
}

function extractEpisodeNumbersByDate(data: any): Record<string, number[]> {
  const out: Record<string, number[]> = {}
  const seasonsFull = Array.isArray(data?.seasons_full) ? data.seasons_full : []
  for (const s of seasonsFull) {
    if (!s || typeof s !== 'object') continue
    if ((Number(s?.season_number) || 0) <= 0) continue
    const eps = Array.isArray(s?.episodes) ? s.episodes : []
    for (const ep of eps) {
      if (!ep || typeof ep !== 'object') continue
      const ad = String(ep?.air_date || '').trim()
      const num = Number(ep?.episode_number) || 0
      if (!ad || num <= 0) continue
      if (!out[ad]) out[ad] = []
      out[ad].push(num)
    }
  }
  for (const key of Object.keys(out)) out[key].sort((a, b) => a - b)
  return out
}

function todayKey(): string {
  return formatYYYYMMDD(new Date())
}

function formatEpisodeList(nums: number[]): string {
  if (!nums.length) return ''
  const sorted = [...nums].sort((a, b) => a - b)
  if (sorted.length === 1) return `E${sorted[0]}`
  const consecutive = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1)
  if (consecutive) return `E${sorted[0]}-${sorted[sorted.length - 1]}`
  return sorted.map((n) => `E${n}`).join('、')
}

function formatMMDD(d: Date) {
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}

function formatYYYYMM(d: Date) {
  const y = String(d.getFullYear())
  const m = String(d.getMonth() + 1).padStart(2, '0')
  return `${y}-${m}`
}

function startOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

function endOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

function addMonths(d: Date, delta: number) {
  return new Date(d.getFullYear(), d.getMonth() + delta, 1)
}

function formatYYYYMMDD(d: Date) {
  const y = String(d.getFullYear())
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function dateOnly(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

function nextDateByWeekdays(after: Date, days: WeekdayKey[]) {
  const base = dateOnly(after)
  const start = new Date(base)
  start.setDate(start.getDate() + 1)
  for (let i = 0; i < 14; i += 1) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    const wd = isoWeekdayFromDate(d)
    if (days.includes(wd)) return d
  }
  const fallback = new Date(start)
  fallback.setDate(start.getDate() + 7)
  return fallback
}

function countEpisodesOnOrBefore(episodeDates: Date[], target: Date) {
  const t = dateOnly(target).getTime()
  let c = 0
  for (const d of episodeDates) {
    if (dateOnly(d).getTime() <= t) c += 1
    else break
  }
  return c
}

function maxEpisodeDate(episodeDates: Date[]) {
  return episodeDates.length ? episodeDates[episodeDates.length - 1] : null
}

function minEpisodeDateAfter(episodeDates: Date[], after: Date) {
  const a = dateOnly(after).getTime()
  for (const d of episodeDates) {
    if (dateOnly(d).getTime() > a) return d
  }
  return null
}

const PREDICT_HORIZON_DAYS = 7

function episodesPerDateAverage(episodeDates: Date[]): number {
  if (!episodeDates.length) return 1
  const counts = new Map<string, number>()
  for (const d of episodeDates) {
    const key = formatYYYYMMDD(d)
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  const keys = Array.from(counts.keys()).sort()
  const recent = keys.slice(-7)
  if (!recent.length) return 1
  const total = recent.reduce((sum, key) => sum + (counts.get(key) || 0), 0)
  return Math.max(1, Math.round(total / recent.length))
}

function predictedAiredTotalForDateUsingTMDBEpisodes(
  date: Date,
  s: { episodeDates: Date[]; totalEpisodes: number | null; weekdays: WeekdayKey[]; cycleDays: number | null },
) {
  const total = s.totalEpisodes
  const target = dateOnly(date)
  if (!s.episodeDates.length) return null

  let known = countEpisodesOnOrBefore(s.episodeDates, target)
  if (total != null) known = Math.min(known, Number(total) || known)

  const maxKnown = maxEpisodeDate(s.episodeDates)
  if (!maxKnown) return known
  if (target.getTime() <= dateOnly(maxKnown).getTime()) return known
  if (!s.weekdays.length) return known
  if (total == null) return known

  const capTotal = Number(total) || known
  const perDay = episodesPerDateAverage(s.episodeDates)
  const horizon = addDays(dateOnly(maxKnown), PREDICT_HORIZON_DAYS)
  let predicted = known
  let cursor = dateOnly(maxKnown)
  while (cursor.getTime() < target.getTime() && predicted < capTotal) {
    if (s.cycleDays && s.weekdays.length === 1) cursor = addDays(cursor, s.cycleDays)
    else cursor = nextDateByWeekdays(cursor, s.weekdays)
    if (cursor.getTime() > horizon.getTime()) break
    if (cursor.getTime() <= target.getTime()) predicted = Math.min(capTotal, predicted + perDay)
    else break
  }
  return predicted
}

function predictedEndAirDateUsingTMDBEpisodes(s: {
  episodeDates: Date[]
  totalEpisodes: number | null
  weekdays: WeekdayKey[]
  cycleDays: number | null
}) {
  if (!s.episodeDates.length) return null
  const total = s.totalEpisodes
  if (total == null || !(Number(total) > 0)) return null
  const capTotal = Number(total)
  const knownCount = s.episodeDates.length
  if (knownCount >= capTotal) {
    const dt = s.episodeDates[capTotal - 1]
    return dt ? formatYYYYMMDD(dt) : null
  }
  const maxKnown = maxEpisodeDate(s.episodeDates)
  if (!maxKnown) return null
  if (!s.weekdays.length) return null
  const perDay = episodesPerDateAverage(s.episodeDates)
  const horizon = addDays(dateOnly(maxKnown), PREDICT_HORIZON_DAYS)
  let cursor = dateOnly(maxKnown)
  let predicted = knownCount
  const cap = Math.min(2000, capTotal)
  while (predicted < cap) {
    if (s.cycleDays && s.weekdays.length === 1) cursor = addDays(cursor, s.cycleDays)
    else cursor = nextDateByWeekdays(cursor, s.weekdays)
    if (cursor.getTime() > horizon.getTime()) return null
    predicted += perDay
    if (predicted >= capTotal) return formatYYYYMMDD(cursor)
  }
  return null
}

function tvPredictedEndAirDate(data: any, days: WeekdayKey[]) {
  if (!data || !days.length) return null
  const status = String(data?.status || '').trim().toLowerCase()
  const endedAt = String(data?.last_episode_to_air?.air_date || data?.last_air_date || '').trim()

  const total = tvTotalEpisodesFromDetail(data)
  const aired = tvAiredEpisodesFromDetail(data, total)
  const nextDateStr = String(data?.next_episode_to_air?.air_date || '').trim()
  const nextDate = nextDateStr ? parseDateOnly(nextDateStr) : null
  if (status === 'ended') {
    if (nextDate) {
      if (total != null && aired != null && Number(total) > Number(aired)) {
        let d = nextDate
        const cap = Math.min(Number(total) - Number(aired), 600)
        for (let i = 1; i < cap; i += 1) {
          d = nextDateByWeekdays(d, days)
        }
        return formatYYYYMMDD(d)
      }
    }
    return endedAt || null
  }

  if (!nextDate) return null
  if (total == null || aired == null) return null
  const remaining = Number(total) - Number(aired)
  if (!Number.isFinite(remaining) || remaining <= 0) return null

  let d = nextDate
  const cap = Math.min(remaining, 600)
  for (let i = 1; i < cap; i += 1) {
    d = nextDateByWeekdays(d, days)
  }
  return formatYYYYMMDD(d)
}

function tvPredictedAiredTotalForDate(
  date: Date,
  s: {
    weekdays: WeekdayKey[]
    baseAiredTotal: number | null
    totalEpisodes: number | null
    nextEpisodeDate: Date | null
    lastEpisodeDate: Date | null
  },
) {
  if (!s.weekdays.length) return null
  if (s.baseAiredTotal == null) return null
  const total = s.totalEpisodes
  const target = dateOnly(date)

  let start: Date | null = null
  if (s.nextEpisodeDate) start = dateOnly(s.nextEpisodeDate)
  else if (s.lastEpisodeDate) start = dateOnly(nextDateByWeekdays(s.lastEpisodeDate, s.weekdays))
  else start = dateOnly(nextDateByWeekdays(new Date(), s.weekdays))

  if (target.getTime() < start.getTime()) return s.baseAiredTotal

  let k = 0
  const d = new Date(start)
  while (d.getTime() <= target.getTime()) {
    if (s.weekdays.includes(isoWeekdayFromDate(d))) k += 1
    d.setDate(d.getDate() + 1)
  }
  let predicted = s.baseAiredTotal + k
  if (total != null) predicted = Math.min(predicted, Number(total) || predicted)
  return predicted
}

function parseDateOnly(yyyyMMdd: string) {
  const s = String(yyyyMMdd || '').trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null
  const [y, m, d] = s.split('-').map((x) => Number(x))
  const dt = new Date(y, (m || 1) - 1, d || 1)
  return Number.isNaN(dt.getTime()) ? null : dt
}

function relativeDayText(target: Date) {
  const a = new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime()
  const b = new Date().setHours(0, 0, 0, 0)
  const diff = Math.round((a - b) / 86400000)
  if (diff === 0) return '今天'
  if (diff === -1) return '1天前'
  if (diff < 0) return `${Math.abs(diff)}天前`
  if (diff === 1) return '1天后'
  return `${diff}天后`
}

async function ensureTMDBDetailCached(tmdbId: number): Promise<TMDBDetail | null> {
  const id = Number(tmdbId) || 0
  if (id <= 0) return null
  if (detailsById[id]) return detailsById[id]
  if (detailPromises[id]) return detailPromises[id]
  detailPromises[id] = (async () => {
    try {
      const data = await fetchTMDBDetail('tv', id)
      try {
        const dates = extractEpisodeDatesFromDetailData(data?.data || {})
        episodeDatesById[id] = dates
        episodeNumbersById[id] = extractEpisodeNumbersByDate(data?.data || {})
        inferredWeekdaysById[id] = inferWeekdaysFromEpisodeDates(dates)
        inferredCycleDaysById[id] = inferCycleDaysFromEpisodeDates(dates)
        const obj: any = data?.data
        if (obj && typeof obj === 'object') {
          delete obj.seasons_full
          delete obj.seasons_full_meta
        }
      } catch {}
      detailsById[id] = data
      return data
    } catch {
      return null
    } finally {
      delete detailPromises[id]
    }
  })()
  return detailPromises[id]
}

const enabledDramaTasks = computed(() =>
  tasks.value.filter((t) => t.task_type === 'drama' && t.enabled && t.tmdb_id && String(t.tmdb_media_type || '').toLowerCase() === 'tv'),
)

const schedules = computed(() => {
  const list: Array<{
    task: TaskItem
    tmdbId: number | null
    title: string
    posterUrl: string
    progressText: string
    progressPercent: number | null
    baseAiredTotal: number | null
    totalEpisodes: number | null
    nextEpisodeDate: Date | null
    lastEpisodeDate: Date | null
    nextAirDate: string | null
    endAirDate: string | null
    episodeDates: Date[]
    cycleDays: number | null
    weekdays: WeekdayKey[]
    savedEpisode: number | null
    episodeNumbersByDate: Record<string, number[]>
  }> = []

  for (const task of enabledDramaTasks.value) {
    const tmdbId = Number(task.tmdb_id) || 0
    const mt = String(task.tmdb_media_type || '').toLowerCase()
    const detail = mt === 'tv' && tmdbId > 0 ? detailsById[tmdbId] : null
    const tmdbWeekdays = normalizeWeekdays(detail?.update_weekdays)
    const episodeDates = mt === 'tv' && tmdbId > 0 ? episodeDatesById[tmdbId] || [] : []
    const inferredWeekdays = mt === 'tv' && tmdbId > 0 ? inferredWeekdaysById[tmdbId] || [] : []
    const runWeekdays = normalizeWeekdays(task.extra?.runweek)
    const days = inferredWeekdays.length ? inferredWeekdays : tmdbWeekdays.length ? tmdbWeekdays : runWeekdays
    if (!days.length) continue

    const title = displayTitle(task, detail || null)
    const posterUrl = posterUrlFromDetail(detail || null)
    const episodeNumbersByDate = mt === 'tv' && tmdbId > 0 ? episodeNumbersById[tmdbId] || {} : {}
    const totalEpisodes = (() => {
      const data = detail?.data || null
      if (!data || mt !== 'tv') return null
      return tvTotalEpisodesFromDetail(data)
    })()
    const baseAiredTotal = (() => {
      const data = detail?.data || null
      if (!data || mt !== 'tv') return null
      return tvAiredTotalFromDetail(data)
    })()
    const nextEpisodeDate = (() => {
      const data = detail?.data || null
      if (!data || mt !== 'tv') return null
      if (episodeDates.length) {
        const d = minEpisodeDateAfter(episodeDates, new Date())
        if (d) return d
      }
      const s = String(data?.next_episode_to_air?.air_date || '').trim()
      return s ? parseDateOnly(s) : null
    })()
    const lastEpisodeDate = (() => {
      const data = detail?.data || null
      if (!data || mt !== 'tv') return null
      if (episodeDates.length) {
        const today = dateOnly(new Date())
        let last: Date | null = null
        for (const d of episodeDates) {
          if (dateOnly(d).getTime() <= today.getTime()) last = d
          else break
        }
        if (last) return last
      }
      const s = String(data?.last_episode_to_air?.air_date || '').trim()
      return s ? parseDateOnly(s) : null
    })()
    const endAirDate = (() => {
      const data = detail?.data || null
      if (!data || mt !== 'tv') return null
      if (episodeDates.length) {
        const status = String(data?.status || '').trim().toLowerCase()
        if (status !== 'ended' && status !== 'canceled') {
          // 连载中：不按"最后已知日期"截断，否则会挡住 7 天内的预测
          return null
        }
        return (
          predictedEndAirDateUsingTMDBEpisodes({
            episodeDates,
            totalEpisodes,
            weekdays: days,
            cycleDays: days.length === 1 ? inferredCycleDaysById[Number(tmdbId)] || null : null,
          }) || tvPredictedEndAirDate(data, days)
        )
      }
      return tvPredictedEndAirDate(data, days)
    })()
    const progressPercent = (() => {
      const data = detail?.data || null
      if (!data) return null
      if (mt !== 'tv') return null
      return tvProgressPercentFromDetail(data)
    })()
    const progressText = (() => {
      const data = detail?.data || null
      if (!data) return ''
      if (mt !== 'tv') return ''
      const brief = tvProgressBriefFromDetail(data)
      return brief || progressFromDetail(task, detail || null)
    })()
    const nextAirDate = nextAirDateFromDetail(detail || null)
    const savedEpisode = (() => {
      const latest = (task.extra as any)?.saved_latest
      const ep = Number(latest?.episode)
      return Number.isFinite(ep) && ep > 0 ? ep : null
    })()

    list.push({
      task,
      tmdbId: mt === 'tv' && tmdbId > 0 ? tmdbId : null,
      title,
      posterUrl,
      progressText,
      progressPercent,
      baseAiredTotal,
      totalEpisodes,
      nextEpisodeDate,
      lastEpisodeDate,
      nextAirDate,
      endAirDate,
      episodeDates,
      cycleDays: days.length === 1 ? inferredCycleDaysById[Number(tmdbId)] || null : null,
      weekdays: days,
      savedEpisode,
      episodeNumbersByDate,
    })
  }

  return list
})

const unknownTasks = computed(() => {
  const list: TaskItem[] = []
  for (const task of enabledDramaTasks.value) {
    const tmdbId = Number(task.tmdb_id) || 0
    const mt = String(task.tmdb_media_type || '').toLowerCase()
    const detail = mt === 'tv' && tmdbId > 0 ? detailsById[tmdbId] : null
    const days = normalizeWeekdays(detail?.update_weekdays)
    const fallback = normalizeWeekdays(task.extra?.runweek)
    if (!days.length && !fallback.length) list.push(task)
  }
  return list
})

const weekDates = computed(() => {
  const out: Date[] = []
  const start = dateOnly(new Date())
  for (let i = 0; i < 7; i += 1) out.push(addDays(start, i))
  return out
})


const DRIVE_ORDER: DriveType[] = [
  'quark',
  'baidu',
  'aliyun',
  '115',
  'uc',
  '123pan',
  'xunlei',
  'cloud189',
  'cloud139',
  'guangya',
]

function scheduleGroupKey(s: { tmdbId: number | null; title: string }): string {
  return s.tmdbId ? `tmdb:${s.tmdbId}` : `title:${String(s.title || '').toLowerCase()}`
}

function driveRankOf(task: TaskItem): number {
  const drive = detectDriveTypeByUrl(String(task.shareurl || ''))
  if (!drive) return 99
  const idx = DRIVE_ORDER.indexOf(drive)
  return idx < 0 ? 98 : idx
}

function driveLabelOf(task: TaskItem): string {
  const drive = detectDriveTypeByUrl(String(task.shareurl || ''))
  return drive ? DRIVE_TYPE_META[drive].label : '网盘'
}

const weekEntriesByDate = computed(() => {
  const by = new Map<string, Map<string, CalendarEntry>>()
  const start = weekDates.value[0] ? dateOnly(weekDates.value[0]) : dateOnly(new Date())
  const end = weekDates.value[6] ? dateOnly(weekDates.value[6]) : addDays(start, 6)

  const push = (d: Date, s: (typeof schedules.value)[number], confirmed: boolean) => {
    const endAir = s.endAirDate ? parseDateOnly(s.endAirDate) : null
    if (endAir && d.getTime() > endAir.getTime()) return

    const key = formatYYYYMMDD(d)
    const map = by.get(key) || new Map<string, CalendarEntry>()
    const groupKey = scheduleGroupKey(s)

    const predictedAiredTotal = s.episodeDates.length
      ? predictedAiredTotalForDateUsingTMDBEpisodes(d, {
          episodeDates: s.episodeDates,
          totalEpisodes: s.totalEpisodes,
          weekdays: s.weekdays,
          cycleDays: s.cycleDays,
        })
      : tvPredictedAiredTotalForDate(d, {
          weekdays: s.weekdays,
          baseAiredTotal: s.baseAiredTotal,
          totalEpisodes: s.totalEpisodes,
          nextEpisodeDate: s.nextEpisodeDate,
          lastEpisodeDate: s.lastEpisodeDate,
        })
    const predictedPercent =
      predictedAiredTotal != null && s.totalEpisodes != null && Number(s.totalEpisodes) > 0
        ? Math.max(0, Math.min(100, Math.floor((predictedAiredTotal / Number(s.totalEpisodes)) * 100)))
        : null
    const totalCount = s.totalEpisodes != null && Number(s.totalEpisodes) > 0 ? Number(s.totalEpisodes) : null
    const savedEpisode = s.savedEpisode
    const savedPercent =
      savedEpisode != null && totalCount != null
        ? Math.max(0, Math.min(100, Math.floor((savedEpisode / totalCount) * 100)))
        : null
    const savedText =
      savedEpisode != null
        ? `已存 ${savedEpisode}${totalCount != null && totalCount >= savedEpisode ? ` / ${totalCount}` : ''}`
        : s.progressText

    const progressItem = {
      key: `${groupKey}:${s.task.id}`,
      rank: driveRankOf(s.task),
      label: driveLabelOf(s.task),
      percent: savedPercent ?? predictedPercent ?? s.progressPercent,
      text: savedText,
    }

    const existing = map.get(groupKey)
    if (existing) {
      existing.progressItems.push(progressItem)
      existing.progressItems.sort((a, b) => a.rank - b.rank)
      by.set(key, map)
      return
    }

    const entryDateKey = formatYYYYMMDD(d)
    map.set(groupKey, {
      weekday: isoWeekdayFromDate(d),
      dateKey: entryDateKey,
      episodeNumbers: s.episodeNumbersByDate[entryDateKey] || [],
      taskId: s.task.id,
      tmdbId: s.tmdbId,
      task: s.task,
      title: s.title,
      posterUrl: s.posterUrl,
      progressText: savedText,
      progressPercent: progressItem.percent,
      predictedAiredTotal: savedEpisode ?? predictedAiredTotal,
      predictedEpisodes: predictedAiredTotal,
      predictedPercent: savedPercent ?? predictedPercent,
      totalEpisodes: s.totalEpisodes,
      nextAirDate: s.nextAirDate,
      confirmed,
      progressItems: [progressItem],
    })
    by.set(key, map)
  }

  for (const s of schedules.value) {
    const keys = new Map<string, boolean>()
    const hasEpisodes = s.episodeDates.length > 0

    if (hasEpisodes) {
      for (const ed of s.episodeDates) {
        const dd = dateOnly(ed)
        if (dd.getTime() < start.getTime() || dd.getTime() > end.getTime()) continue
        keys.set(formatYYYYMMDD(dd), true)
      }

      const total = s.totalEpisodes
      if (total != null && Number(total) > 0 && s.weekdays.length) {
        let knownEpisodes = s.episodeDates.length
        if (knownEpisodes < Number(total)) {
          let cursor = dateOnly(maxEpisodeDate(s.episodeDates) || new Date())
          const perDay = episodesPerDateAverage(s.episodeDates)
          const horizon = addDays(cursor, PREDICT_HORIZON_DAYS)
          const cap = Math.min(2000, Number(total))
          while (knownEpisodes < cap) {
            if (s.cycleDays && s.weekdays.length === 1) cursor = addDays(cursor, s.cycleDays)
            else cursor = nextDateByWeekdays(cursor, s.weekdays)
            if (cursor.getTime() > horizon.getTime()) break
            knownEpisodes += perDay
            if (cursor.getTime() > end.getTime()) break
            if (cursor.getTime() >= start.getTime()) {
              const k = formatYYYYMMDD(cursor)
              if (!keys.has(k)) keys.set(k, false)
            }
          }
        }
      }
    } else {
      const air = s.nextAirDate
      const airDate = air ? parseDateOnly(air) : null
      if (airDate && airDate.getTime() >= start.getTime() && airDate.getTime() <= end.getTime()) keys.set(formatYYYYMMDD(airDate), true)
      for (const d of weekDates.value) {
        const wd = isoWeekdayFromDate(d)
        if (!s.weekdays.includes(wd)) continue
        const k = formatYYYYMMDD(d)
        if (!keys.has(k)) keys.set(k, false)
      }
    }

    for (const [k, confirmed] of keys) {
      const d = parseDateOnly(k)
      if (d) push(d, s, confirmed)
    }
  }

  const out = new Map<string, CalendarEntry[]>()
  for (const [k, map] of by.entries()) {
    const list = Array.from(map.values())
    list.sort((a, b) => a.title.localeCompare(b.title))
    out.set(k, list)
  }
  return out
})

const weekSections = computed(() => {
  return weekDates.value
    .map((d) => {
      const key = formatYYYYMMDD(d)
      const w = weekdays.find((x) => x.key === isoWeekdayFromDate(d))!
      const items = (weekEntriesByDate.value.get(key) || []).slice()
      return { key, label: w.label, dateText: `${formatMMDD(d)} · ${relativeDayText(d)}`, items }
    })
    .filter((x) => x.items.length)
})

const monthTitle = computed(() => formatYYYYMM(monthCursor.value))

const monthGridDates = computed(() => {
  const first = startOfMonth(monthCursor.value)
  const last = endOfMonth(monthCursor.value)
  const start = new Date(first)
  start.setDate(start.getDate() - (isoWeekdayFromDate(start) - 1))
  const end = new Date(last)
  end.setDate(end.getDate() + (7 - isoWeekdayFromDate(end)))
  const out: Date[] = []
  const d = new Date(start)
  while (d.getTime() <= end.getTime()) {
    out.push(new Date(d))
    d.setDate(d.getDate() + 1)
  }
  return out
})

const monthEntriesByDate = computed(() => {
  const cur = monthCursor.value
  const ym = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}`
  const by = new Map<string, Map<string, CalendarEntry>>()

  const push = (d: Date, s: (typeof schedules.value)[number], confirmed: boolean) => {
    if (d.getFullYear() !== cur.getFullYear() || d.getMonth() !== cur.getMonth()) return
    const end = s.endAirDate ? parseDateOnly(s.endAirDate) : null
    if (end && d.getTime() > end.getTime()) return
    const key = formatYYYYMMDD(d)
    if (!key.startsWith(ym)) return
    const map = by.get(key) || new Map<string, CalendarEntry>()
    const groupKey = scheduleGroupKey(s)

    const predictedAiredTotal = s.episodeDates.length
      ? predictedAiredTotalForDateUsingTMDBEpisodes(d, {
          episodeDates: s.episodeDates,
          totalEpisodes: s.totalEpisodes,
          weekdays: s.weekdays,
          cycleDays: s.cycleDays,
        })
      : tvPredictedAiredTotalForDate(d, {
          weekdays: s.weekdays,
          baseAiredTotal: s.baseAiredTotal,
          totalEpisodes: s.totalEpisodes,
          nextEpisodeDate: s.nextEpisodeDate,
          lastEpisodeDate: s.lastEpisodeDate,
        })
    const predictedPercent =
      predictedAiredTotal != null && s.totalEpisodes != null && Number(s.totalEpisodes) > 0
        ? Math.max(0, Math.min(100, Math.floor((predictedAiredTotal / Number(s.totalEpisodes)) * 100)))
        : null

    const totalCount = s.totalEpisodes != null && Number(s.totalEpisodes) > 0 ? Number(s.totalEpisodes) : null
    const savedEpisode = s.savedEpisode
    const savedPercent =
      savedEpisode != null && totalCount != null
        ? Math.max(0, Math.min(100, Math.floor((savedEpisode / totalCount) * 100)))
        : null
    const savedText =
      savedEpisode != null
        ? `已存 ${savedEpisode}${totalCount != null && totalCount >= savedEpisode ? ` / ${totalCount}` : ''}`
        : s.progressText

    const progressItem = {
      key: `${groupKey}:${s.task.id}`,
      rank: driveRankOf(s.task),
      label: driveLabelOf(s.task),
      percent: savedPercent ?? predictedPercent ?? s.progressPercent,
      text: savedText,
    }

    const existing = map.get(groupKey)
    if (existing) {
      existing.progressItems.push(progressItem)
      existing.progressItems.sort((a, b) => a.rank - b.rank)
      by.set(key, map)
      return
    }

    const entryDateKey = formatYYYYMMDD(d)
    map.set(groupKey, {
      weekday: isoWeekdayFromDate(d),
      dateKey: entryDateKey,
      episodeNumbers: s.episodeNumbersByDate[entryDateKey] || [],
      taskId: s.task.id,
      tmdbId: s.tmdbId,
      task: s.task,
      title: s.title,
      posterUrl: s.posterUrl,
      progressText: savedText,
      progressPercent: progressItem.percent,
      predictedAiredTotal: savedEpisode ?? predictedAiredTotal,
      predictedEpisodes: predictedAiredTotal,
      predictedPercent: savedPercent ?? predictedPercent,
      totalEpisodes: s.totalEpisodes,
      nextAirDate: s.nextAirDate,
      confirmed,
      progressItems: [progressItem],
    })
    by.set(key, map)
  }

  for (const s of schedules.value) {
    const monthStart = startOfMonth(cur)
    const monthEnd = endOfMonth(cur)

    const hasEpisodes = s.episodeDates && s.episodeDates.length
    if (hasEpisodes) {
      const keys = new Map<string, boolean>()
      for (const ed of s.episodeDates) {
        if (ed.getTime() < monthStart.getTime() || ed.getTime() > monthEnd.getTime()) continue
        keys.set(formatYYYYMMDD(ed), true)
      }

      const total = s.totalEpisodes
      if (total != null && Number(total) > 0 && s.weekdays.length) {
        let knownEpisodes = s.episodeDates.length
        if (knownEpisodes < Number(total)) {
          let cursor = dateOnly(maxEpisodeDate(s.episodeDates) || new Date())
          const perDay = episodesPerDateAverage(s.episodeDates)
          const horizon = addDays(cursor, PREDICT_HORIZON_DAYS)
          let cap = Math.min(2000, Number(total))
          while (knownEpisodes < cap) {
            if (s.cycleDays && s.weekdays.length === 1) cursor = addDays(cursor, s.cycleDays)
            else cursor = nextDateByWeekdays(cursor, s.weekdays)
            if (cursor.getTime() > horizon.getTime()) break
            knownEpisodes += perDay
            if (cursor.getTime() > monthEnd.getTime()) break
            if (cursor.getTime() >= monthStart.getTime()) {
              const k = formatYYYYMMDD(cursor)
              if (!keys.has(k)) keys.set(k, false)
            }
          }
        }
      }

      for (const [k, confirmed] of keys) {
        const d = parseDateOnly(k)
        if (d) push(d, s, confirmed)
      }
      continue
    }

    const air = s.nextAirDate
    const airDate = air ? parseDateOnly(air) : null
    const keys = new Map<string, boolean>()
    for (const d of monthGridDates.value) {
      if (d.getFullYear() !== cur.getFullYear() || d.getMonth() !== cur.getMonth()) continue
      const wd = isoWeekdayFromDate(d)
      if (!s.weekdays.includes(wd)) continue
      const k = formatYYYYMMDD(d)
      if (!keys.has(k)) keys.set(k, false)
    }
    if (airDate) keys.set(formatYYYYMMDD(airDate), true)
    for (const [k, confirmed] of keys) {
      const d = parseDateOnly(k)
      if (d) push(d, s, confirmed)
    }
  }

  const out = new Map<string, CalendarEntry[]>()
  for (const [k, map] of by.entries()) {
    const list = Array.from(map.values())
    list.sort((a, b) => a.title.localeCompare(b.title))
    out.set(k, list)
  }
  return out
})

const monthCells = computed(() => {
  const cur = monthCursor.value
  const todayKey = formatYYYYMMDD(new Date())

  const cells = monthGridDates.value.map((d) => {
    const key = formatYYYYMMDD(d)
    const list = (monthEntriesByDate.value.get(key) || []).slice()
    const inMonth = d.getFullYear() === cur.getFullYear() && d.getMonth() === cur.getMonth()
    const isToday = key === todayKey
    const display = list.slice(0, 3)
    const more = Math.max(0, list.length - display.length)
    return {
      key,
      date: d,
      day: d.getDate(),
      inMonth,
      isToday,
      items: display,
      more,
      total: list.length,
    }
  })
  return cells
})

function prevMonth() {
  monthCursor.value = addMonths(monthCursor.value, -1)
}

function nextMonth() {
  monthCursor.value = addMonths(monthCursor.value, 1)
}

function goToday() {
  monthCursor.value = startOfMonth(new Date())
}

const monthMoreDialog = reactive({
  visible: false,
  title: '',
  items: [] as CalendarEntry[],
})

const selectedItems = computed(() => (selectedDateKey.value ? monthEntriesByDate.value.get(selectedDateKey.value) || [] : []))
const selectedDateTitle = computed(() => {
  const d = selectedDateKey.value ? parseDateOnly(selectedDateKey.value) : null
  return d ? formatYYYYMMDD(d) : ''
})

function openMonthMore(dateKey: string) {
  const d = parseDateOnly(dateKey)
  monthMoreDialog.title = d ? `${formatYYYYMMDD(d)}` : dateKey
  monthMoreDialog.items = (monthEntriesByDate.value.get(dateKey) || []).slice()
  monthMoreDialog.visible = true
}

function setDefaultSelectedDate() {
  const cur = monthCursor.value
  const today = new Date()
  const isCurMonth = cur.getFullYear() === today.getFullYear() && cur.getMonth() === today.getMonth()
  if (isCurMonth) {
    selectedDateKey.value = formatYYYYMMDD(today)
    return
  }
  const keys = Array.from(monthEntriesByDate.value.keys()).sort()
  selectedDateKey.value = keys[0] || formatYYYYMMDD(cur)
}

function onResize() {
  viewport.width = window.innerWidth
}

function handleSelectCell(cell: { key: string; inMonth: boolean }) {
  if (!cell.inMonth) return
  selectedDateKey.value = cell.key
}

async function loadData() {
  loading.value = true
  try {
    const all = await fetchTasks()
    tasks.value = all

    const ids = Array.from(
      new Set(
        all
          .filter((t) => t.task_type === 'drama' && t.enabled)
          .filter((t) => String(t.tmdb_media_type || '').toLowerCase() === 'tv')
          .map((t) => Number(t.tmdb_id) || 0)
          .filter((x) => x > 0),
      ),
    )

    await runWithConcurrency(ids, 6, async (id) => {
      await ensureTMDBDetailCached(id)
    })
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function handleRefresh() {
  for (const url of Object.keys(posterFailures)) delete posterFailures[url]
  for (const k of Object.keys(detailsById)) delete detailsById[Number(k)]
  for (const k of Object.keys(detailPromises)) delete detailPromises[Number(k)]
  invalidateTMDBDetailCache()
  await loadData()
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})

watch(
  () => [monthCursor.value.getFullYear(), monthCursor.value.getMonth()],
  () => {
    setDefaultSelectedDate()
  },
  { immediate: true },
)

watch(
  () => monthEntriesByDate.value,
  () => {
    if (!selectedDateKey.value) setDefaultSelectedDate()
  },
  { immediate: true },
)
</script>

<template>
  <div class="page">
    <div class="header">
      <div class="title-wrap">
        <div class="title">追剧日历</div>
        <div class="subtitle">按更新星期查看本周与本月排期，预计日期会标注。</div>
      </div>
      <div class="actions">
        <el-radio-group v-model="viewMode" size="small" class="mode">
          <el-radio-button label="week">周视图</el-radio-button>
          <el-radio-button label="month">月视图</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" type="primary" @click="handleRefresh">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="unknownTasks.length"
      type="warning"
      :closable="false"
      show-icon
      class="warn"
      :title="`有 ${unknownTasks.length} 个启用追剧任务未能判断更新日（未关联 TMDB 且未配置 runweek）`"
    />

    <div v-if="viewMode === 'week'" v-loading="loading" class="week">
      <section v-for="sec in weekSections" :key="sec.key" class="weekday">
        <div class="weekday-title">
          <div class="weekday-title__left">
            <div class="weekday-title__main">{{ sec.label }}</div>
            <div class="weekday-title__sub">{{ sec.dateText }}</div>
          </div>
          <div class="weekday-title__count">{{ sec.items.length }}</div>
        </div>

        <div v-if="!sec.items.length" class="weekday-empty">无更新</div>
        <div v-else class="weekday-grid">
          <el-card v-for="it in sec.items" :key="`${it.taskId}-${it.weekday}-${it.tmdbId || 0}`" shadow="hover" class="show-card" :class="{ 'show-card--predicted': !it.confirmed }">
            <div class="poster-card">
              <img v-if="it.posterUrl && posterFailures[it.posterUrl] !== 'unavailable'" class="poster-img" :src="posterSource(it.posterUrl)" @error="handlePosterError(it.posterUrl)" :alt="`${it.title} 海报`" loading="lazy" decoding="async" />
              <div v-else class="poster-fallback">暂无海报</div>
              <span v-if="it.dateKey === todayKey() && it.episodeNumbers.length" class="poster-badge poster-badge--ep">
                今日 {{ formatEpisodeList(it.episodeNumbers) }}
              </span>
              <span v-else-if="it.dateKey > todayKey() && it.confirmed && it.episodeNumbers.length" class="poster-badge">
                {{ formatEpisodeList(it.episodeNumbers) }}
              </span>
              <span v-else-if="it.dateKey > todayKey()" class="poster-badge">
                预测{{ it.predictedEpisodes != null ? ` E${it.predictedEpisodes}` : '' }}
              </span>

              <div class="poster-overlay">
                <div class="overlay-title" :title="it.title">{{ it.title }}</div>
                <div class="poster-progress-list" :tabindex="it.progressItems.length > 2 ? 0 : undefined" :aria-label="`${it.title} 网盘进度`">
                  <div v-for="p in it.progressItems" :key="p.key" class="overlay-progress">
                    <div class="overlay-progress__meta">
                      <span class="overlay-drive">{{ p.label }}</span>
                      <span class="percent">{{ p.text || (p.percent != null ? `${p.percent}%` : '暂无进度') }}</span>
                    </div>
                    <el-progress
                      v-if="p.percent != null"
                      :percentage="p.percent"
                      :stroke-width="3"
                      :show-text="false"
                      :aria-label="`${p.label}：${p.text || `${p.percent}%`}`"
                    />
                  </div>
                </div>
              </div>
            </div>
          </el-card>
        </div>
      </section>
    </div>

    <div v-else v-loading="loading" class="month">
      <div class="month-bar">
        <div class="month-title">{{ monthTitle }}</div>
        <div class="month-legend">
          <span class="month-legend__dot" />预计 = 按最近更新规律推测（最多 7 天）
        </div>
        <div class="month-nav">
          <el-button text bg @click="goToday">回今天</el-button>
          <el-button circle text bg @click="prevMonth">
            <el-icon><ArrowLeft /></el-icon>
          </el-button>
          <el-button circle text bg @click="nextMonth">
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </div>

      <div class="month-weekdays">
        <div v-for="w in weekdays" :key="w.key" class="month-weekday">{{ w.label }}</div>
      </div>

      <div class="month-grid">
        <div
          v-for="cell in monthCells"
          :key="cell.key"
          class="month-cell"
          :class="{ 'is-other': !cell.inMonth, 'is-today': cell.isToday, 'is-selected': isMobile && selectedDateKey === cell.key }"
          @click="isMobile ? handleSelectCell(cell) : null"
        >
          <div class="month-day">{{ cell.day }}</div>
          <div v-if="isMobile" class="month-mobile-indicator">
            <span v-if="cell.total" class="month-dot" />
            <span v-if="cell.total" class="month-count">{{ cell.total }}</span>
          </div>
          <div v-else class="month-items">
            <div
              v-for="it in cell.items"
              :key="`${cell.key}-${it.taskId}`"
              class="month-item"
              :class="{ 'is-predicted': !it.confirmed }"
              :title="it.confirmed ? it.title : `${it.title}（预计更新）`"
            >
              <img v-if="it.posterUrl" class="month-poster" :src="it.posterUrl" alt="poster" />
              <div v-else class="month-poster-fallback">TMDB</div>
              <div class="month-name" :title="it.title">{{ it.title }}</div>
              <div class="month-pct">
                <template v-if="it.dateKey > todayKey() && it.confirmed && it.episodeNumbers.length">{{ formatEpisodeList(it.episodeNumbers) }}</template>
                <template v-else-if="it.dateKey > todayKey()">预测{{ it.predictedEpisodes != null ? ` E${it.predictedEpisodes}` : '' }}</template>
                <template v-else-if="it.dateKey === todayKey() && it.episodeNumbers.length">{{ formatEpisodeList(it.episodeNumbers) }}</template>
                <template v-else-if="it.episodeNumbers.length">{{ formatEpisodeList(it.episodeNumbers) }}</template>
                <template v-else-if="it.progressItems.length > 1">{{ it.progressItems.length }} 盘</template>
                <template v-else>{{ it.predictedAiredTotal != null ? `E${it.predictedAiredTotal}` : it.progressPercent != null ? `${it.progressPercent}%` : '—' }}</template>
              </div>
            </div>
            <el-button v-if="cell.more" text class="month-more" @click.stop="openMonthMore(cell.key)">+{{ cell.more }}部</el-button>
          </div>
        </div>
      </div>

      <div v-if="isMobile" class="month-detail">
        <div class="month-detail__title">
          <div>{{ selectedDateTitle || '请选择日期' }}</div>
          <div class="month-detail__count">{{ selectedItems.length }}</div>
        </div>
        <div v-if="!selectedItems.length" class="month-detail__empty">当天无更新</div>
        <div v-else class="month-detail__list">
          <div v-for="it in selectedItems" :key="`sel-${selectedDateKey}-${it.taskId}`" class="month-detail__item">
            <img v-if="it.posterUrl" class="month-detail__poster" :src="it.posterUrl" alt="poster" />
            <div v-else class="month-detail__poster-fallback">TMDB</div>
            <div class="month-detail__main">
              <div class="month-detail__name" :title="it.title">{{ it.title }}</div>
              <div class="month-detail__progress">
                <el-progress
                  v-if="it.predictedPercent != null || it.progressPercent != null"
                  :percentage="it.predictedPercent ?? it.progressPercent ?? 0"
                  :stroke-width="6"
                  :show-text="false"
                />
                <div class="month-detail__pct">{{ it.predictedPercent != null ? `${it.predictedPercent}%` : it.progressPercent != null ? `${it.progressPercent}%` : '—' }}</div>
              </div>
              <div v-if="it.predictedAiredTotal != null" class="month-detail__caption">预计更新到：E{{ it.predictedAiredTotal }}</div>
              <div v-else-if="it.progressText" class="month-detail__caption">{{ it.progressText }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <el-dialog v-if="!isMobile" v-model="monthMoreDialog.visible" :title="monthMoreDialog.title" width="720px">
      <div class="month-dialog">
        <div v-for="it in monthMoreDialog.items" :key="`dlg-${monthMoreDialog.title}-${it.taskId}`" class="month-dialog-item">
          <img v-if="it.posterUrl" class="month-dialog-poster" :src="it.posterUrl" alt="poster" />
          <div v-else class="month-dialog-poster-fallback">TMDB</div>
          <div class="month-dialog-name" :title="it.title">{{ it.title }}</div>
          <div class="month-dialog-pct">
            {{ it.predictedAiredTotal != null ? `E${it.predictedAiredTotal}` : it.progressPercent != null ? `${it.progressPercent}%` : '—' }}
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>


<style scoped>
.page {
  padding: 0;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.title-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subtitle {
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

@media (max-width: 767px) {
  .title {
    font-size: 19px;
  }
}

.warn {
  margin-bottom: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode :deep(.el-radio-button__inner) {
  border-radius: 10px;
}

.week {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.weekday-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 2px;
}

.weekday-title__left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.weekday-title__main {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.2px;
}

.weekday-title__sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.weekday-title__count {
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
}

.weekday-empty {
  opacity: 0.6;
  font-size: 13px;
}

.weekday-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}

.show-card {
  min-width: 0;
  overflow: hidden;
  transition: transform 220ms ease, box-shadow 220ms ease;
}

.show-card :deep(.el-card__body) { padding: 0; }

.poster-card {
  position: relative;
  width: 100%;
  aspect-ratio: 2 / 3;
  overflow: hidden;
  border-radius: var(--panel-radius);
  background: #202734;
  isolation: isolate;
}

.poster-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 400ms ease;
}

.poster-fallback {
  display: grid;
  place-items: center;
  height: 100%;
  color: #aebace;
  font-size: 13px;
  letter-spacing: .12em;
}

.poster-overlay {
  position: absolute;
  inset: auto 0 0;
  padding: 48px 14px 14px;
  background: linear-gradient(180deg, transparent, rgba(8, 12, 21, .55) 30%, rgba(8, 12, 21, .9) 70%, rgba(8, 12, 21, .97));
  color: #fff;
}

.overlay-title {
  margin-bottom: 10px;
  font-size: 16px;
  font-weight: 650;
  line-height: 1.4;
  text-shadow: 0 1px 8px rgba(0, 0, 0, .35);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.poster-progress-list {
  display: grid;
  gap: 10px;
  max-height: 82px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, .35) transparent;
}

.overlay-progress { display: grid; gap: 7px; min-width: 0; }
.overlay-progress__meta { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; }
.overlay-drive { color: #d0d8e4; font-size: 11px; line-height: 1.4; }
.percent { color: #fff; font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums; line-height: 1.4; text-align: right; }
.overlay-progress :deep(.el-progress-bar__outer) { background: rgba(255, 255, 255, .2); }
.overlay-progress :deep(.el-progress-bar__inner) { background: #58c99a; }

@media (hover: hover) and (pointer: fine) {
  .show-card:hover { transform: translateY(-3px); box-shadow: 0 10px 24px rgba(15, 23, 42, .14) !important; }
  .show-card:hover .poster-img { transform: scale(1.025); }
}

@media (prefers-reduced-motion: reduce) {
  .show-card, .poster-img { transition: none; }
  .show-card:hover, .show-card:hover .poster-img { transform: none; }
}

.month {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.month-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.month-title {
  font-size: 18px;
  font-weight: 700;
}

.month-nav {
  display: flex;
  gap: 8px;
}

.month-weekdays {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 10px;
  padding: 4px 2px 0;
}

.month-weekday {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.month-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 10px;
}

.month-cell {
  min-height: 120px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.month-cell.is-other {
  opacity: 0.45;
}

.month-cell.is-today {
  border-color: rgba(59, 130, 246, 0.55);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.12) inset;
}

.month-cell.is-selected {
  border-color: rgba(34, 197, 94, 0.7);
  box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.14) inset;
}

.month-day {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.month-mobile-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: auto;
}

.month-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.92);
  box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.14);
}

.month-count {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

.month-detail {
  margin-top: 10px;
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  padding: 12px;
}

.month-detail__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-weight: 700;
}

.month-detail__count {
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
}

.month-detail__empty {
  margin-top: 10px;
  opacity: 0.6;
  font-size: 13px;
}

.month-detail__list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.month-detail__item {
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: 12px;
  align-items: start;
}

.month-detail__poster {
  width: 56px;
  height: 78px;
  border-radius: 12px;
  object-fit: cover;
  display: block;
  background: rgba(148, 163, 184, 0.14);
}

.month-detail__poster-fallback {
  width: 56px;
  height: 78px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  opacity: 0.55;
  background: rgba(148, 163, 184, 0.14);
}

.month-detail__main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.month-detail__name {
  font-size: 14px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.month-detail__progress {
  display: flex;
  align-items: center;
  gap: 10px;
}

.month-detail__progress :deep(.el-progress) {
  flex: 1 1 auto;
  min-width: 0;
}

.month-detail__progress :deep(.el-progress-bar__outer) {
  background: rgba(15, 23, 42, 0.12);
}

.month-detail__progress :deep(.el-progress-bar__inner) {
  background: rgba(34, 197, 94, 0.92);
}

.month-detail__pct {
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  opacity: 0.9;
}

.month-detail__caption {
  font-size: 12px;
  opacity: 0.75;
}

.month-items {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
}

.month-item {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.month-poster {
  width: 20px;
  height: 28px;
  border-radius: 6px;
  object-fit: cover;
  display: block;
  background: rgba(148, 163, 184, 0.14);
}

.month-poster-fallback {
  width: 20px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  opacity: 0.55;
  background: rgba(148, 163, 184, 0.14);
}

.month-name {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.month-pct {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

.month-more {
  padding: 0;
  height: auto;
  font-size: 12px;
  opacity: 0.8;
}

.month-dialog {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.month-dialog-item {
  display: grid;
  grid-template-columns: 34px 1fr auto;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.month-dialog-poster {
  width: 34px;
  height: 48px;
  border-radius: 10px;
  object-fit: cover;
  display: block;
  background: rgba(148, 163, 184, 0.14);
}

.month-dialog-poster-fallback {
  width: 34px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  opacity: 0.55;
  background: rgba(148, 163, 184, 0.14);
}

.month-dialog-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.month-dialog-pct {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

@media (max-width: 1400px) {
  .weekday-grid {
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  }
}

@media (max-width: 900px) {
  .weekday-grid {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 14px;
  }

  .month-weekdays {
    gap: 6px;
  }

  .month-grid {
    gap: 6px;
  }

  .month-cell {
    min-height: 56px;
    padding: 8px;
    border-radius: 12px;
    cursor: pointer;
  }

  .month-day {
    font-size: 11px;
  }
}

@media (max-width: 520px) {
  .header { flex-direction: column; align-items: stretch; gap: 12px; }
  .actions { justify-content: space-between; }
  .mode { display: inline-flex; flex-wrap: nowrap; }
  .weekday-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .poster-overlay { padding: 36px 10px 12px; }
  .overlay-title { font-size: 14px; margin-bottom: 8px; }
  .poster-progress-list { max-height: 74px; gap: 8px; }
  .weekday-title__main {
    font-size: 20px;
  }

  .month-weekday {
    font-size: 11px;
  }

  .month-cell {
    min-height: 52px;
    padding: 7px;
  }
}
.poster-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  background: rgba(15, 23, 42, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.28);
  backdrop-filter: blur(6px);
}

.month-item.is-predicted {
  opacity: 0.62;
}

.month-item.is-predicted .month-name {
  font-style: italic;
}

.month-legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.month-legend__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px dashed var(--el-text-color-secondary);
  opacity: 0.7;
}



.poster-badge--ep {
  background: rgba(47, 111, 237, 0.85);
  border-color: rgba(255, 255, 255, 0.4);
}
</style>
