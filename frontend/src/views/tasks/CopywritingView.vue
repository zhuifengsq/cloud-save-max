<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { fetchTMDBCredits, fetchTMDBDetail } from '@/api/media'
import type { TMDBCredits } from '@/types/media'
import { copyText as writeClipboard } from '@/utils/clipboard'
import { categoryFromTmdb, episodeUnitFor } from '@/utils/mediaCategory'
import { buildWpsText, isPremiereToday } from '@/utils/wpsCopy'

type Draft = {
  title: string
  year: string
  tmdbId: number | null
  mediaType: 'movie' | 'tv'
  category: string
  posterPath: string
  overview: string
  episode: string
  links: { drive: string; url: string }[]
  taskname: string
  savedAt: number
}

const router = useRouter()

const emptyDraft: Draft = {
  title: '',
  year: '',
  tmdbId: null,
  mediaType: 'tv',
  category: '',
  posterPath: '',
  overview: '',
  episode: '',
  links: [],
  taskname: '',
  savedAt: 0,
}

const draft = reactive<Draft>({ ...emptyDraft })
const loaded = ref(false)
const editingText = ref('')
const textEdited = ref(false)
const posterLoading = ref(false)
const posterError = ref(false)

const TMDB_POSTER_BASE = 'https://image.tmdb.org/t/p/original'
const TMDB_POSTER_PREVIEW = 'https://image.tmdb.org/t/p/w780'

const posterUrl = computed(() => {
  const path = draft.posterPath.trim()
  if (!path) return ''
  return TMDB_POSTER_PREVIEW + (path.startsWith('/') ? path : `/${path}`)
})

const fullPosterUrl = computed(() => {
  const path = draft.posterPath.trim()
  if (!path) return ''
  return TMDB_POSTER_BASE + (path.startsWith('/') ? path : `/${path}`)
})

// 综艺按「期」计数，其他按「集」
const episodeUnit = computed(() => episodeUnitFor(draft.category))

const episodeText = computed(() =>
  draft.episode.trim() ? `【更${draft.episode.trim()}${episodeUnit.value}】` : '',
)

const titleLine = computed(() => {
  const title = draft.title.trim() || draft.taskname.trim() || '未命名'
  const year = draft.year.trim()
  return year ? `${title} (${year}) ${episodeText.value}`.trim() : `${title} ${episodeText.value}`.trim()
})

function driveLabel(drive: string): string {
  const map: Record<string, string> = {
    quark: '夸克',
    baidu: '百度',
    '115': '115',
    aliyun: '阿里',
    uc: 'UC',
    '123pan': '123',
    cloud189: '天翼',
    cloud139: '移动',
    xunlei: '迅雷',
    guangya: '光鸭',
  }
  return map[String(drive || '').toLowerCase()] || '链接'
}

function addLink() {
  draft.links.push({ drive: '', url: '' })
}

function removeLink(index: number) {
  draft.links.splice(index, 1)
}

function buildCopyText(): string {
  const lines: string[] = [titleLine.value, '']
  const urls = draft.links.map((item) => item.url.trim()).filter(Boolean)
  for (const url of urls) lines.push(`链接：${url}`)
  if (urls.length) lines.push('')
  const overview = draft.overview.trim()
  if (overview) lines.push(`简介：${overview}`)
  return lines.join('\n').trim()
}

function regenerate() {
  editingText.value = buildCopyText()
  textEdited.value = false
}

onMounted(() => {
  const raw = sessionStorage.getItem('copywriting:draft')
  if (!raw) {
    loaded.value = true
    return
  }
  try {
    const parsed = JSON.parse(raw) as Partial<Draft>
    Object.assign(draft, emptyDraft, parsed)
    if (!Array.isArray(draft.links)) draft.links = []
  } catch {
    /* ignore */
  }
  syncWpsFromDraft()
  loaded.value = true
  regenerate()
  // 页面一进来就开始预取 WPS 需要的详情/演员，切标签时不用等
  loadWpsMeta()

  if (draft.tmdbId && (!draft.posterPath || !draft.category)) {
    void loadPosterFromTMDB()
  }
})

async function loadPosterFromTMDB() {
  if (!draft.tmdbId) return
  posterLoading.value = true
  try {
    const detail = await fetchTMDBDetail(draft.mediaType, Number(draft.tmdbId))
    const data = (detail as any)?.data || {}
    const path = String(data.poster_path || '').trim()
    if (path) draft.posterPath = path
    // 旧草稿没有 category 时，按 TMDB 分类补上，决定文案里用「集」还是「期」
    if (!draft.category && Array.isArray(data.genres)) {
      draft.category = categoryFromTmdb(draft.mediaType, data)
      syncWpsTypeFromCategory()
    }
  } catch {
    /* ignore */
  } finally {
    posterLoading.value = false
  }
}

async function copyText() {
  const text = editingText.value || buildCopyText()
  if (await writeClipboard(text)) {
    ElMessage.success('文案已复制')
  } else {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('image load failed'))
    img.src = url
  })
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

function posterFileName(ext: string): string {
  const base = (draft.title || draft.taskname || '海报').replace(/[\\/:*?"<>|]/g, '').trim() || '海报'
  return `${base}-海报.${ext}`
}

const posterRawBlob = ref<Blob | null>(null)
const posterPngBlob = ref<Blob | null>(null)
const posterPreparing = ref(false)
const posterDialogVisible = ref(false)

async function fetchPosterBlob(): Promise<{ blob: Blob; ext: string } | null> {
  const url = fullPosterUrl.value
  if (!url) return null
  try {
    const resp = await fetch(url, { mode: 'cors' })
    if (!resp.ok) throw new Error(String(resp.status))
    const blob = await resp.blob()
    const type = blob.type || 'image/jpeg'
    const ext = type.includes('png') ? 'png' : type.includes('webp') ? 'webp' : 'jpg'
    return { blob, ext }
  } catch {
    // fetch 失败（跨域限制等）时退回 img + canvas
    try {
      const img = await loadImage(url)
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) return null
      ctx.drawImage(img, 0, 0)
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.95))
      return blob ? { blob, ext: 'jpg' } : null
    } catch {
      return null
    }
  }
}

async function blobToPng(blob: Blob): Promise<Blob | null> {
  try {
    const bitmap = await createImageBitmap(blob)
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(bitmap, 0, 0)
    bitmap.close?.()
    return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  } catch {
    return null
  }
}

async function preparePoster(): Promise<void> {
  if (!fullPosterUrl.value) {
    posterRawBlob.value = null
    posterPngBlob.value = null
    return
  }
  posterPreparing.value = true
  try {
    const poster = await fetchPosterBlob()
    if (!poster) return
    posterRawBlob.value = poster.blob
    // 剪贴板只接受 image/png，先把 JPEG 转成 PNG 缓存好
    posterPngBlob.value =
      poster.blob.type === 'image/png' ? poster.blob : await blobToPng(poster.blob)
  } finally {
    posterPreparing.value = false
  }
}

watch(
  fullPosterUrl,
  () => {
    void preparePoster()
  },
  { immediate: true },
)

function canWriteImage(): boolean {
  if (typeof window === 'undefined') return false
  return (
    window.isSecureContext &&
    typeof ClipboardItem !== 'undefined' &&
    Boolean(navigator.clipboard?.write)
  )
}

async function copyPoster() {
  if (!posterPngBlob.value && !posterPreparing.value && !posterRawBlob.value) {
    await preparePoster()
  }
  const png = posterPngBlob.value
  if (!png) {
    ElMessage.error('海报获取失败')
    return
  }
  if (canWriteImage()) {
    try {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })])
      ElMessage.success('海报已复制，可以直接粘贴')
      return
    } catch {
      /* 继续走弹窗兜底 */
    }
  }
  posterDialogVisible.value = true
}

async function downloadPoster() {
  if (!posterRawBlob.value) await preparePoster()
  const blob = posterRawBlob.value || posterPngBlob.value
  if (!blob) {
    ElMessage.error('海报获取失败')
    return
  }
  downloadBlob(blob, posterFileName(blob.type.includes('png') ? 'png' : 'jpg'))
}

const mode = ref<'weibo' | 'qq' | 'wps'>('weibo')
const weiboQuality = ref('4K高码')
const weiboGuide = ref('宝宝们自取点点攒攒[心][心]')

const WEIBO_TAG: Record<string, string> = {
  quark: '夸克',
  baidu: '度',
  aliyun: '阿',
  '115': '115',
  uc: 'UC',
  '123pan': '123',
  xunlei: '雷',
  cloud189: '天',
  cloud139: '移',
  guangya: '光',
}

const WEIBO_PREFIX: Record<string, string> = {
  quark: '垮',
  baidu: '渡',
  aliyun: '阿',
  '115': '115',
  uc: 'UC',
  '123pan': '123',
  xunlei: '雷',
  cloud189: '天',
  cloud139: '移',
  guangya: '光',
}

const weiboDriveTags = computed(() => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const link of draft.links) {
    if (!link.url.trim()) continue
    const tag = WEIBO_TAG[String(link.drive || '').toLowerCase()] || '链'
    if (seen.has(tag)) continue
    seen.add(tag)
    out.push(tag)
  }
  return out
})

const weiboLine1 = computed(() => {
  const title = draft.title.trim() || draft.taskname.trim() || '未命名'
  const episode = draft.episode.trim()
  const quality = weiboQuality.value.trim() || '4K高码'
  return `${title} ${episode ? `更${episode}${episodeUnit.value}` : ''}资源${quality}【已更】`
})

const weiboText1 = computed(() => {
  const drivePart = weiboDriveTags.value.length ? `（${weiboDriveTags.value.join('➕')}）` : ''
  return `${weiboLine1.value}\n${drivePart}👍🏻攒取⬇️`
})

const weiboLinksText = computed(() => {
  const lines: string[] = []
  for (const link of draft.links) {
    const url = link.url.trim()
    if (!url) continue
    const prefix = WEIBO_PREFIX[String(link.drive || '').toLowerCase()] || '链'
    lines.push(`${prefix}：${url}`)
  }
  return lines.join('\n')
})

// ---- WPS 文案：🔥国剧「剧名 首播更新04/30集」4K【演员】 + 百度/夸克链接 ----
const WPS_TYPE_OPTIONS = ['国剧', '美剧', '日剧', '韩剧', '港剧', '台剧', '泰剧', '电影', '不标类型']

const wpsType = ref('国剧')
const wpsEpisode = ref('')
const wpsTotalEpisodes = ref('')
const wpsQuality = ref('4K')
const wpsCast = ref('')
const wpsPhrase = ref<'首播更新' | '更新'>('更新')
const wpsMetaLoaded = ref(false)
const wpsCastLoading = ref(false)

const wpsTypeLabel = computed(() => (wpsType.value === '不标类型' ? '' : wpsType.value))

// 综艺、动漫不写主演；综艺文案格式为「更3期」，不带总期数和话术
const wpsHideCast = computed(() => draft.category === '综艺' || draft.category === '动漫')
const wpsIsVariety = computed(() => episodeUnit.value === '期')

const wpsText = computed(() =>
  buildWpsText({
    typeLabel: wpsTypeLabel.value,
    title: draft.title.trim() || draft.taskname.trim() || '未命名',
    phrase: wpsPhrase.value,
    episode: wpsEpisode.value,
    totalEpisodes: wpsTotalEpisodes.value,
    unit: episodeUnit.value,
    quality: wpsQuality.value,
    cast: wpsHideCast.value ? '' : wpsCast.value,
    links: draft.links,
  }),
)

// 动漫/综艺不标类型；电影直接写「电影」
function syncWpsTypeFromCategory() {
  if (draft.category === '综艺' || draft.category === '动漫') {
    wpsType.value = '不标类型'
  } else if (draft.category === '电影') {
    wpsType.value = '电影'
  }
}

function syncWpsFromDraft() {
  wpsEpisode.value = draft.episode.trim()
  syncWpsTypeFromCategory()
}

// 详情走缓存（毫秒级），credits 是 TMDB 网络请求（约 1~2 秒），两者并行预取；
// 详情先回来立即填集数/话术，主演后回来再补，切到 WPS 标签时通常已经就绪
let wpsDetailPromise: Promise<Record<string, any> | null> | null = null
let wpsCreditsPromise: Promise<TMDBCredits | null> | null = null

function ensureWpsDetail() {
  if (!wpsDetailPromise) {
    const mid = Number(draft.tmdbId)
    wpsDetailPromise =
      mid > 0
        ? fetchTMDBDetail(draft.mediaType, mid)
            .then((detail) => ((detail as any)?.data || {}) as Record<string, any>)
            .catch(() => null)
        : Promise.resolve(null)
  }
  return wpsDetailPromise
}

function ensureWpsCredits() {
  if (!wpsCreditsPromise) {
    const mid = Number(draft.tmdbId)
    wpsCreditsPromise =
      mid > 0
        ? fetchTMDBCredits(draft.mediaType, mid, { limit: 2 }).catch(() => null)
        : Promise.resolve(null)
  }
  return wpsCreditsPromise
}

function applyWpsDetail(data: Record<string, any> | null) {
  if (!data) return
  const seasons = Array.isArray(data.seasons) ? data.seasons : []
  const lastSeasonNumber = Number(data?.last_episode_to_air?.season_number || 0)
  const season =
    seasons.find((item: any) => Number(item?.season_number) === (lastSeasonNumber || 1)) || seasons[0] || null

  if (!wpsTotalEpisodes.value) {
    const total = Number(season?.episode_count || 0) || Number(data.number_of_episodes || 0) || 0
    if (total > 0) wpsTotalEpisodes.value = String(total)
  }
  if (!wpsEpisode.value) {
    const aired = Number(data?.last_episode_to_air?.episode_number || 0)
    if (aired > 0) wpsEpisode.value = String(aired)
  }
  if (!draft.category && Array.isArray(data.genres)) {
    draft.category = categoryFromTmdb(draft.mediaType, data)
    syncWpsTypeFromCategory()
  }

  // 首播 = 最新一集就是今天更新的；否则一律叫「更新」
  const airDate = String(
    data?.last_episode_to_air?.air_date || data.first_air_date || data.release_date || '',
  )
  wpsPhrase.value = isPremiereToday(airDate) ? '首播更新' : '更新'
}

function applyWpsCredits(credits: TMDBCredits | null) {
  if (!credits || wpsCast.value || wpsHideCast.value) return
  const names = (credits.cast || [])
    .map((item) => String(item?.name || '').trim())
    .filter(Boolean)
    .slice(0, 2)
  if (names.length) wpsCast.value = names.join(' ')
}

// 幂等启动预取，不阻塞页面加载；结果到了直接回填
function loadWpsMeta() {
  if (wpsMetaLoaded.value || !Number(draft.tmdbId)) return
  wpsMetaLoaded.value = true
  void ensureWpsDetail().then(applyWpsDetail)
  if (!wpsHideCast.value) {
    wpsCastLoading.value = true
    void ensureWpsCredits().then((credits) => {
      applyWpsCredits(credits)
      wpsCastLoading.value = false
    })
  }
}

watch(mode, (value) => {
  if (value === 'wps') loadWpsMeta()
})

async function copyBlock(text: string, label: string) {
  if (!text.trim()) {
    ElMessage.warning('内容为空')
    return
  }
  if (await writeClipboard(text)) {
    ElMessage.success(`${label}已复制`)
  } else {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

function goBack() {
  router.back()
}
</script>

<template>
  <div class="shell-page copywriting-page">
    <div class="section-header">
      <div class="section-header__title">
        <div class="section-header__eyebrow">Copywriting</div>
        <h2>生成文案</h2>
        <div class="section-header__desc">修改内容后复制文案或海报，全部在本地完成，不上传服务器。</div>
      </div>
      <div class="toolbar__right">
        <el-button @click="goBack">返回</el-button>
      </div>
    </div>

    <div v-if="!loaded" class="glass-panel copy-card" v-loading="true" style="min-height: 240px" />

    <div v-else-if="!draft.title && !draft.taskname" class="glass-panel copy-card">
      <div class="empty-state">
        <div class="empty-state__title">没有可用的任务数据</div>
        <div class="empty-state__desc">请回到任务抽屉，关联 TMDB 后点击「生成文案」。</div>
        <div class="empty-state__actions"><el-button type="primary" @click="goBack">返回任务</el-button></div>
      </div>
    </div>

    <div v-else class="copy-grid">
      <section class="glass-panel copy-card copy-poster">
        <div class="copy-poster__frame">
          <el-image
            v-if="posterUrl"
            :src="posterUrl"
            fit="cover"
            class="copy-poster__img"
            @error="posterError = true"
          />
          <div v-else class="copy-poster__empty">暂无海报</div>
        </div>
        <div class="copy-poster__meta">
          <span>{{ draft.category || (draft.mediaType === 'movie' ? '电影' : '剧集') }}</span>
          <span v-if="draft.tmdbId">TMDB #{{ draft.tmdbId }}</span>
        </div>
        <el-button text :loading="posterLoading" @click="loadPosterFromTMDB">重新获取海报</el-button>
      </section>

      <section class="glass-panel copy-card copy-main">
        <div class="copy-tabs">
          <button type="button" class="copy-tab" :class="{ 'is-active': mode === 'weibo' }" @click="mode = 'weibo'">微博</button>
          <button type="button" class="copy-tab" :class="{ 'is-active': mode === 'qq' }" @click="mode = 'qq'">QQ频道</button>
          <button type="button" class="copy-tab" :class="{ 'is-active': mode === 'wps' }" @click="mode = 'wps'">WPS</button>
        </div>

        <template v-if="mode === 'weibo'">
          <div class="copy-fields">
            <div class="copy-field copy-field--title">
              <label>剧名</label>
              <el-input v-model="draft.title" placeholder="剧名" />
            </div>
            <div class="copy-field copy-field--episode">
              <label>更几{{ episodeUnit }}</label>
              <el-input v-model="draft.episode" placeholder="未知，可手动填写" />
            </div>
            <div class="copy-field copy-field--year">
              <label>画质</label>
              <el-input v-model="weiboQuality" placeholder="4K高码" />
            </div>
          </div>

          <div class="copy-field">
            <div class="copy-field__head">
              <label>分享链接</label>
              <div class="copy-field__actions">
                <el-button size="small" text @click="addLink">+ 添加链接</el-button>
              </div>
            </div>
            <div v-if="draft.links.length" class="copy-links">
              <div v-for="(link, idx) in draft.links" :key="idx" class="copy-link-row">
                <span class="copy-link-tag">{{ driveLabel(link.drive) }}</span>
                <el-input v-model="link.url" placeholder="https://pan.quark.cn/s/..." />
                <el-button text size="small" @click="removeLink(idx)">删除</el-button>
              </div>
            </div>
            <div v-else class="copy-links__empty">还没有链接，点右上角「+ 添加链接」</div>
          </div>

          <div class="weibo-block">
            <div class="weibo-block__head">
              <span class="weibo-block__title">正文</span>
              <el-button size="small" type="primary" text @click="copyBlock(weiboText1, '正文')">复制</el-button>
            </div>
            <pre class="weibo-block__body">{{ weiboText1 }}</pre>
          </div>

          <div class="weibo-block">
            <div class="weibo-block__head">
              <span class="weibo-block__title">互动</span>
              <el-button size="small" type="primary" text @click="copyBlock(weiboGuide, '互动')">复制</el-button>
            </div>
            <el-input v-model="weiboGuide" type="textarea" :rows="2" class="weibo-guide" />
          </div>

          <div class="weibo-block">
            <div class="weibo-block__head">
              <span class="weibo-block__title">链接区</span>
              <el-button size="small" type="primary" text @click="copyBlock(weiboLinksText, '链接')">复制</el-button>
            </div>
            <pre v-if="weiboLinksText" class="weibo-block__body">{{ weiboLinksText }}</pre>
            <div v-else class="copy-links__empty">填好链接后自动生成</div>
          </div>

          <div class="copy-actions">
            <el-button :disabled="!posterUrl" @click="copyPoster">复制海报</el-button>
            <el-button text :disabled="!posterUrl" @click="downloadPoster">下载海报</el-button>
          </div>
        </template>

        <template v-else-if="mode === 'qq'">
          <div class="copy-fields">
            <div class="copy-field copy-field--title">
              <label>标题</label>
              <el-input v-model="draft.title" placeholder="剧名" />
            </div>
            <div class="copy-field copy-field--year">
              <label>年份</label>
              <el-input v-model="draft.year" placeholder="2026" />
            </div>
            <div class="copy-field copy-field--episode">
              <label>更新{{ episodeUnit }}数</label>
              <el-input v-model="draft.episode" placeholder="未知，可手动填写" />
            </div>
          </div>

          <div class="copy-field">
            <div class="copy-field__head">
              <label>分享链接</label>
              <div class="copy-field__actions">
                <el-button size="small" text @click="addLink">+ 添加链接</el-button>
              </div>
            </div>
            <div v-if="draft.links.length" class="copy-links">
              <div v-for="(link, idx) in draft.links" :key="idx" class="copy-link-row">
                <span class="copy-link-tag">{{ driveLabel(link.drive) }}</span>
                <el-input v-model="link.url" placeholder="https://pan.quark.cn/s/..." />
                <el-button text size="small" @click="removeLink(idx)">删除</el-button>
              </div>
            </div>
            <div v-else class="copy-links__empty">还没有链接，点右上角「+ 添加链接」</div>
          </div>

          <div class="copy-field">
            <label>简介</label>
            <el-input v-model="draft.overview" type="textarea" :rows="4" placeholder="剧情简介" />
          </div>

          <div class="copy-field">
            <div class="copy-field__head">
              <label>文案</label>
              <div class="copy-field__actions">
                <el-button size="small" text @click="regenerate">重新生成</el-button>
              </div>
            </div>
            <el-input
              v-model="editingText"
              type="textarea"
              :rows="7"
              class="copy-text"
              @input="textEdited = true"
            />
          </div>

          <div class="copy-actions">
            <el-button type="primary" @click="copyText">复制文案</el-button>
            <el-button :disabled="!posterUrl" @click="copyPoster">复制海报</el-button>
            <el-button text :disabled="!posterUrl" @click="downloadPoster">下载海报</el-button>
          </div>
        </template>

        <template v-else>
          <div class="copy-fields">
            <div class="copy-field copy-field--title">
              <label>剧名</label>
              <el-input v-model="draft.title" placeholder="剧名" />
            </div>
            <div class="copy-field copy-field--wps-type">
              <label>类型</label>
              <el-select v-model="wpsType" placeholder="类型">
                <el-option v-for="item in WPS_TYPE_OPTIONS" :key="item" :label="item" :value="item" />
              </el-select>
            </div>
          </div>

          <div class="copy-fields">
            <div class="copy-field copy-field--episode">
              <label>当前{{ episodeUnit }}</label>
              <el-input v-model="wpsEpisode" placeholder="04" />
            </div>
            <div v-if="!wpsIsVariety" class="copy-field copy-field--episode">
              <label>总{{ episodeUnit }}数</label>
              <el-input v-model="wpsTotalEpisodes" placeholder="30" />
            </div>
            <div class="copy-field copy-field--year">
              <label>画质</label>
              <el-input v-model="wpsQuality" placeholder="4K" />
            </div>
            <div v-if="!wpsIsVariety" class="copy-field copy-field--episode">
              <label>话术</label>
              <el-select v-model="wpsPhrase">
                <el-option label="首播更新" value="首播更新" />
                <el-option label="更新" value="更新" />
              </el-select>
            </div>
          </div>

          <div v-if="!wpsHideCast" class="copy-field">
            <label>主演（空格分隔）</label>
            <el-input
              v-model="wpsCast"
              :placeholder="wpsCastLoading ? '正在获取主演…' : '井柏然 孙艺洲'"
            />
          </div>

          <div class="copy-field">
            <div class="copy-field__head">
              <label>分享链接</label>
              <div class="copy-field__actions">
                <el-button size="small" text @click="addLink">+ 添加链接</el-button>
              </div>
            </div>
            <div v-if="draft.links.length" class="copy-links">
              <div v-for="(link, idx) in draft.links" :key="idx" class="copy-link-row">
                <span class="copy-link-tag">{{ driveLabel(link.drive) }}</span>
                <el-input v-model="link.url" placeholder="https://pan.quark.cn/s/..." />
                <el-button text size="small" @click="removeLink(idx)">删除</el-button>
              </div>
            </div>
            <div v-else class="copy-links__empty">还没有链接，点右上角「+ 添加链接」</div>
            <div class="copy-field__hint">文案里按 百度 → 夸克 → 其他 的顺序输出</div>
          </div>

          <div class="weibo-block">
            <div class="weibo-block__head">
              <span class="weibo-block__title">文案</span>
              <el-button size="small" type="primary" text @click="copyBlock(wpsText, '文案')">复制</el-button>
            </div>
            <pre class="weibo-block__body">{{ wpsText }}</pre>
          </div>

          <div class="copy-actions">
            <el-button type="primary" @click="copyBlock(wpsText, '文案')">复制文案</el-button>
            <el-button :disabled="!posterUrl" @click="copyPoster">复制海报</el-button>
            <el-button text :disabled="!posterUrl" @click="downloadPoster">下载海报</el-button>
          </div>
        </template>
      </section>
    </div>
  </div>

    <el-dialog v-model="posterDialogVisible" title="复制海报" width="520px" style="max-width: 94vw" append-to-body>
      <div class="poster-copy-dialog">
        <img v-if="posterUrl" :src="posterUrl" class="poster-copy-dialog__img" alt="海报" />
        <div class="poster-copy-dialog__hint">
          浏览器当前环境不允许直接写剪贴板，请<strong>右键图片 → 复制图片</strong>（手机上长按图片保存）。
        </div>
        <div class="poster-copy-dialog__actions">
          <el-button type="primary" @click="downloadPoster">下载海报</el-button>
          <el-button @click="posterDialogVisible = false">关闭</el-button>
        </div>
      </div>
    </el-dialog>
</template>

<style scoped>
.copy-grid {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.copy-card {
  padding: 20px 22px;
}

.copy-poster {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: stretch;
}

.copy-poster__frame {
  width: 100%;
  aspect-ratio: 2 / 3;
  border-radius: var(--panel-radius);
  overflow: hidden;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
}

.copy-poster__img {
  width: 100%;
  height: 100%;
  display: block;
}

.copy-poster__empty {
  display: grid;
  place-items: center;
  height: 100%;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

.copy-poster__meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.copy-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.copy-fields {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.copy-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.copy-field--title {
  flex: 2;
}

.copy-field--year,
.copy-field--episode {
  flex: 0 0 110px;
}

.copy-field--wps-type {
  flex: 0 0 128px;
}

.copy-field > label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.copy-field__hint {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  white-space: nowrap;
}

.copy-field__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.copy-field__actions {
  display: flex;
  gap: 6px;
}

.copy-links {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.copy-link-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.copy-link-tag {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 11.5px;
  font-weight: 650;
}

.copy-links__empty {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.copy-text :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.7;
}

.copy-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 4px;
}

@media (max-width: 960px) {
  .copy-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .copy-poster {
    max-width: 320px;
  }
}

.poster-copy-dialog {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.poster-copy-dialog__img {
  max-width: 100%;
  max-height: 60vh;
  border-radius: 10px;
  border: 1px solid var(--el-border-color-lighter);
}

.poster-copy-dialog__hint {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.poster-copy-dialog__actions {
  display: flex;
  gap: 10px;
}

.copy-tabs {
  display: flex;
  gap: 24px;
  margin: -4px 0 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.copy-tab {
  position: relative;
  padding: 8px 2px 10px;
  border: 0;
  background: transparent;
  font-size: 15px;
  font-weight: 500;
  color: var(--el-text-color-regular);
  cursor: pointer;
}

.copy-tab.is-active {
  color: var(--el-color-primary);
  font-weight: 600;
}

.copy-tab.is-active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  border-radius: 2px;
  background: var(--el-color-primary);
}

.weibo-block {
  margin-top: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  overflow: hidden;
  background: var(--el-fill-color-blank);
}

.weibo-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.weibo-block__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.weibo-block__body {
  margin: 0;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.8;
  color: var(--el-text-color-primary);
}

.weibo-guide {
  padding: 8px 10px;
}
</style>
