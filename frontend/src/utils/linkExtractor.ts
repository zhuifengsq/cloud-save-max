export type ExtractedLink = { title: string; url: string }

export const DEFAULT_QUARK_SUFFIX = '【资源汇总，不断更新中】\nhttps://www.kdocs.cn/l/cp3suTalsbnS\n\n文档没有请搜（网站新增百度网盘搜索）\nhttps://www.254777.icu'
export const DEFAULT_HOT_PREFIX = '🔥 今日热播剧更新'
export const DEFAULT_HOT_SUFFIX = '【资源汇总，不断更新中】\nhttps://kdocs.cn/l/cp3suTalsbnS'

export function normalizeSource(text: string): string {
  return text.replace(/(?:&#x20;|&#32;|&nbsp;)/gi, ' ')
    .replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ')
    .replace(/\\(?=[ \t]*$)/gm, '').replace(/\\[ \t]+/g, ' ')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

function cleanUrl(url: string): string {
  return url.replace(/[\\\])）】》>，,。；;]+$/g, '').trim()
}

function cleanTitle(title: string): string {
  return title.replace(/(?:提取码|密码).*$/, '')
    .replace(/\s*[（(]\d{4}[）)]/g, '').replace(/\s*【.*?】/g, '').replace(/\s*\[.*?\]/g, '').trim()
}

export function extractTable(input: string): ExtractedLink[] {
  const text = normalizeSource(input)
    .replace(/^["“”]+|["“”]+$/gm, '')
    .replace(/我用.*?分享了「(.*?)」/g, '$1')
    .replace(/，?点击链接即可保存。打开「.*?APP」，无需下载在线播放视频，畅享原画.*?支持电视投屏。/g, '')
    .replace(/复制这段内容后打开.*?App/gi, '')
    .replace(/点击链接保存.*?，或复制本段内容/gi, '')
    .replace(/打开【.*?】/gi, '').replace(/来自.*?分享/g, '')
  const rows: ExtractedLink[] = []
  let title = ''
  for (const line of text.split('\n').map((s) => s.trim()).filter(Boolean)) {
    const matches = [...line.matchAll(/https?:\/\/[^\s<>"'“”]+/gi)]
    if (matches.length) {
      const prefix = line.slice(0, matches[0]!.index)
        .replace(/(?:夸克|阿里(?:云盘)?|百度(?:网盘)?|迅雷|网盘|分享|网页)?(?:链接)?[：: \t]*$/i, '').trim()
      const inlineTitle = cleanTitle(prefix)
      if (inlineTitle) title = inlineTitle
      for (const match of matches) rows.push({ title: title || '未匹配到影视名称', url: cleanUrl(match[0]) })
    } else if (!/^(?:提取码|解压码|密码)/.test(line)) {
      title = cleanTitle(line)
    }
  }
  return rows
}

export function filterQuark(input: string, suffix: string): string {
  const blocks: string[] = []
  let title = ''
  let links: string[] = []
  const flush = () => { if (title && links.length) blocks.push(`${title}\n${links.join('\n')}`) }
  for (const line of normalizeSource(input).split('\n').map((s) => s.trim()).filter(Boolean)) {
    if (line.includes('夸克') || line.includes('quark.cn')) links.push(line)
    else if (/百度|baidu\.com|迅雷|阿里|aliyundrive|^(?:提取码|解压码|密码)/.test(line)) continue
    else { flush(); title = line; links = [] }
  }
  flush()
  return [blocks.join('\n\n'), suffix.trim()].filter(Boolean).join('\n\n')
}

export function organizeDualDisk(input: string) {
  const text = normalizeSource(input)
  const baidu = new Set<string>()
  const quark = new Set<string>()
  for (const raw of text.match(/https?:\/\/[^\s<>"'“”]+/gi) || []) {
    const url = cleanUrl(raw)
    try {
      const host = new URL(url).hostname.toLowerCase()
      if (host === 'pan.baidu.com' || host.endsWith('.pan.baidu.com')) baidu.add(url)
      else if (host === 'pan.quark.cn' || host.endsWith('.pan.quark.cn')) quark.add(url)
    } catch { /* Skip malformed URLs. */ }
  }
  const title = text.split('\n').map((s) => s.trim())
    .find((line) => line && !/https?:\/\//i.test(line) && !/^(?:链接|百度(?:网盘)?|夸克(?:网盘)?)[：:]/.test(line)) || ''
  const lines = title ? [title] : []
  baidu.forEach((url) => lines.push(`百度链接：${url}`))
  quark.forEach((url) => lines.push(`夸克链接：${url}`))
  return { text: lines.join('\n'), baiduCount: baidu.size, quarkCount: quark.size }
}

export function generateHotUpdates(input: string, prefix: string, suffix: string) {
  const entries = new Set<string>()
  const text = normalizeSource(input).replace(/[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]/g, '')
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line || /https?:\/\//i.test(line) || /^(?:百度|夸克)(?:网盘)?链接[：:]/.test(line)) continue
    const match = line.match(/^(.+?)(?:\s*[（(]\s*\d{4}\s*[）)])?\s*(【[^】]*集[^】]*】)\s*$/)
    if (!match) continue
    const parts = match[1]!.trim().split(/\s+/).filter(Boolean)
    const title = parts.length > 1 && parts.every((s) => /^[\u3400-\u9FFF·]+$/.test(s)) ? parts[0]! : parts.join('')
    if (title) entries.add(title + match[2]!.replace(/\s+/g, ''))
  }
  const lines: string[] = []
  if (prefix.trim()) lines.push(prefix.trim(), '')
  lines.push(...entries)
  if (suffix.trim()) lines.push(suffix.trim())
  return { text: lines.join('\n'), count: entries.size }
}

// Read pasted markup as inert text, preserving hyperlink targets from WPS/Word.
export function clipboardHtmlToText(html: string): string {
  if (!html) return ''
  const doc = new DOMParser().parseFromString(html, 'text/html')
  if (!doc.querySelector('a[href]')) return ''
  const blocks = new Set(['ADDRESS', 'ARTICLE', 'BLOCKQUOTE', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'LI', 'P', 'PRE', 'SECTION', 'TR'])
  const parts: string[] = []
  function append(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) { parts.push(node.nodeValue || ''); return }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    const tag = el.tagName
    if (['SVG', 'SCRIPT', 'STYLE'].includes(tag)) return
    if (tag === 'BR') { parts.push('\n'); return }
    if (tag === 'A') {
      const label = (el.textContent || '').trim()
      const href = el.getAttribute('href') || ''
      parts.push(/^https?:\/\//i.test(href) ? `${label}${label ? ' ' : ''}(${href})` : label)
      return
    }
    if (blocks.has(tag) && parts.length && !parts[parts.length - 1]!.endsWith('\n')) parts.push('\n')
    el.childNodes.forEach(append)
    if (blocks.has(tag)) parts.push('\n')
  }
  append(doc.body)
  return normalizeSource(parts.join(''))
}
