<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { fetchTMDBDetail } from '@/api/media'
import { copyText as writeClipboard } from '@/utils/clipboard'

type Draft = {
  title: string
  year: string
  tmdbId: number | null
  mediaType: 'movie' | 'tv'
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

const episodeText = computed(() => (draft.episode.trim() ? `【更${draft.episode.trim()}集】` : ''))

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
  loaded.value = true
  regenerate()

  if (!draft.posterPath && draft.tmdbId) {
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

const mode = ref<'weibo' | 'qq'>('weibo')
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
  const episode = draft.episode.trim() || '1'
  const quality = weiboQuality.value.trim() || '4K高码'
  return `${title} 更${episode}集资源${quality}【已更】`
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
          <span>{{ draft.mediaType === 'movie' ? '电影' : '剧集' }}</span>
          <span v-if="draft.tmdbId">TMDB #{{ draft.tmdbId }}</span>
        </div>
        <el-button text :loading="posterLoading" @click="loadPosterFromTMDB">重新获取海报</el-button>
      </section>

      <section class="glass-panel copy-card copy-main">
        <div class="copy-tabs">
          <button type="button" class="copy-tab" :class="{ 'is-active': mode === 'weibo' }" @click="mode = 'weibo'">微博</button>
          <button type="button" class="copy-tab" :class="{ 'is-active': mode === 'qq' }" @click="mode = 'qq'">QQ频道</button>
        </div>

        <template v-if="mode === 'weibo'">
          <div class="copy-fields">
            <div class="copy-field copy-field--title">
              <label>剧名</label>
              <el-input v-model="draft.title" placeholder="剧名" />
            </div>
            <div class="copy-field copy-field--episode">
              <label>更几集</label>
              <el-input v-model="draft.episode" placeholder="1" />
              <div class="copy-field__hint">默认取 TMDB 最新已播集，可手动改</div>
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

        <template v-else>
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
              <label>更新集数</label>
              <el-input v-model="draft.episode" placeholder="4" />
              <div class="copy-field__hint">默认取 TMDB 最新已播集</div>
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
