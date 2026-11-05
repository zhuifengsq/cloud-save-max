export type WpsLink = {
  drive: string
  url: string
}

export type WpsTextInput = {
  typeLabel: string
  title: string
  phrase: string
  episode: string
  totalEpisodes: string
  unit: string
  quality: string
  cast: string
  links: WpsLink[]
}

const DRIVE_LABELS: Record<string, string> = {
  baidu: '百度',
  quark: '夸克',
  '115': '115',
  aliyun: '阿里',
  uc: 'UC',
  '123pan': '123',
  cloud189: '天翼',
  cloud139: '移动',
  xunlei: '迅雷',
  guangya: '光鸭',
}

// WPS 文案里链接固定按 百度 → 夸克 → 其他 的顺序输出
const LINK_ORDER = ['baidu', 'quark']

function driveKey(drive: string): string {
  return String(drive || '').trim().toLowerCase()
}

export function wpsDriveLabel(drive: string): string {
  return DRIVE_LABELS[driveKey(drive)] || '其他'
}

export function orderWpsLinks(links: WpsLink[]): WpsLink[] {
  const valid = (Array.isArray(links) ? links : [])
    .filter((item) => String(item?.url || '').trim())
    .map((item, index) => ({ item, index }))
  valid.sort((a, b) => {
    const rankA = LINK_ORDER.indexOf(driveKey(a.item.drive))
    const rankB = LINK_ORDER.indexOf(driveKey(b.item.drive))
    const orderA = rankA >= 0 ? rankA : LINK_ORDER.length
    const orderB = rankB >= 0 ? rankB : LINK_ORDER.length
    return orderA - orderB || a.index - b.index
  })
  return valid.map((entry) => entry.item)
}

export function formatWpsLinks(links: WpsLink[]): string {
  return orderWpsLinks(links)
    .map((item) => `${wpsDriveLabel(item.drive)}链接：${String(item.url).trim()}`)
    .join('\n')
}

function episodeNumber(value: string | number | null | undefined): number | null {
  const digits = String(value ?? '').replace(/[^\d]/g, '')
  if (!digits) return null
  const num = Number(digits)
  if (!Number.isFinite(num) || num <= 0) return null
  return num
}

// 剧集统一补零（04/30），综艺期数保持原样（5/12）
function formatEpisodeNumber(value: string | number | null | undefined, padded: boolean): string {
  const num = episodeNumber(value)
  if (num === null) return ''
  return padded ? String(num).padStart(2, '0') : String(num)
}

export function padEpisode(value: string | number | null | undefined): string {
  return formatEpisodeNumber(value, true)
}

function localDateString(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * 只有「今天更新的那一集」才叫首播：最新一集的播出日期等于今天。
 */
export function isPremiereToday(airDate: string | null | undefined, today: Date = new Date()): boolean {
  const raw = String(airDate || '').trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return false
  return raw === localDateString(today)
}

export function buildWpsHeadline(input: Omit<WpsTextInput, 'links'>): string {
  const title = input.title.trim() || '未命名'
  const unit = input.unit.trim() || '集'
  const isVariety = unit === '期'
  const episode = formatEpisodeNumber(input.episode, !isVariety)
  const total = formatEpisodeNumber(input.totalEpisodes, false)

  let inner = title
  if (isVariety) {
    // 综艺：🔥「后会友期 更3期」，不写总期数
    if (episode) inner += ` 更${episode}${unit}`
    else if (total) inner += ` 全${total}${unit}`
  } else if (episode) {
    const phrase = input.phrase.trim() || '更新'
    inner += ` ${phrase}${episode}${total ? `/${total}` : ''}${unit}`
  } else if (total) {
    inner += ` 全${total}${unit}`
  }

  const typeLabel = input.typeLabel.trim()
  const quality = input.quality.trim()
  // 综艺/动漫不写主演
  const cast = isVariety ? '' : input.cast.trim()
  const castPart = cast ? `【${cast}】` : ''

  return `🔥${typeLabel}「${inner}」${quality}${castPart}`
}

export function buildWpsText(input: WpsTextInput): string {
  const lines = [buildWpsHeadline(input)]
  const linkText = formatWpsLinks(input.links)
  if (linkText) lines.push(linkText)
  return lines.join('\n').trim()
}
