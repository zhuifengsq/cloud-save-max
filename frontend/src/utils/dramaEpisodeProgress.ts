// Shared by the calendar and copywriting: saved progress takes precedence over aired progress.
export function tvTotalEpisodesFromDetail(detail: any): number | null {
  const n = detail?.number_of_episodes
  if (typeof n === 'number' && n > 0) return n
  const seasons = Array.isArray(detail?.seasons) ? detail.seasons : []
  const sum = seasons
    .filter((s: any) => s && typeof s === 'object' && Number(s.season_number) > 0)
    .reduce((acc: number, s: any) => acc + (Number(s.episode_count) || 0), 0)
  return sum > 0 ? sum : null
}

export function tvAiredEpisodesFromDetail(detail: any, total: number | null): number | null {
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

export function resolveDramaEpisodeProgress(extra: Record<string, any> | null | undefined, detail: any) {
  const ep = Number(extra?.saved_latest?.episode)
  const savedEpisode = Number.isFinite(ep) && ep > 0 ? ep : null
  const totalEpisodes = tvTotalEpisodesFromDetail(detail)
  const airedEpisode = tvAiredEpisodesFromDetail(detail, totalEpisodes)
  return { savedEpisode, totalEpisodes, airedEpisode, updatedEpisode: savedEpisode ?? airedEpisode }
}

// Aired episodes are a fallback for display, never proof of files saved to a drive.
export function calendarProgressText(savedEpisode: number | null, totalEpisodes: number | null, airedEpisode: number | null): string {
  const hasSaved = savedEpisode != null && Number.isFinite(savedEpisode) && savedEpisode > 0
  const episode = hasSaved ? savedEpisode : airedEpisode
  if (episode == null || !Number.isFinite(episode) || episode <= 0) return '已存待确认'
  const suffix = totalEpisodes != null && Number.isFinite(totalEpisodes) && totalEpisodes >= episode ? ` / ${totalEpisodes}` : ''
  return `${hasSaved ? '已存' : '已播'} ${episode}${suffix}`
}
