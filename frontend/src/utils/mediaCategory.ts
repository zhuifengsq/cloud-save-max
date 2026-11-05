export type MediaCategory = '电影' | '电视剧' | '动漫' | '综艺'

// TMDB 的类型 id：10764 真人秀、10767 脱口秀、10763 新闻（部分综艺会挂在新闻下）
const VARIETY_GENRE_IDS = new Set([10764, 10767, 10763])

export function categoryFromTmdb(mediaType: string, detail: any): MediaCategory {
  const mt = String(mediaType || '').toLowerCase()
  if (mt === 'movie') return '电影'
  const genres = Array.isArray(detail?.genres) ? detail.genres : []
  const ids = new Set<number>()
  const names = new Set<string>()
  for (const g of genres) {
    const id = Number((g as any)?.id)
    if (Number.isFinite(id) && id > 0) ids.add(id)
    const n = String((g as any)?.name || '').trim().toLowerCase()
    if (n) names.add(n)
  }
  const hasAnime = ids.has(16) || Array.from(names).some((n) => n.includes('animation') || n.includes('动画'))
  if (hasAnime) return '动漫'
  const hasVariety =
    Array.from(VARIETY_GENRE_IDS).some((id) => ids.has(id)) ||
    Array.from(names).some((n) => n.includes('reality') || n.includes('talk') || n.includes('真人秀') || n.includes('脱口秀'))
  if (hasVariety) return '综艺'
  return '电视剧'
}

// 综艺按「期」计数，其他影视按「集」
export function episodeUnitFor(category: string | null | undefined): '集' | '期' {
  return category === '综艺' ? '期' : '集'
}
