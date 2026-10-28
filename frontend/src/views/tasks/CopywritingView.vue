<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { fetchTMDBDetail } from '@/api/media'

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
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    ElMessage.success('文案已复制')
  } catch {
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

function wrapLines(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const out: string[] = []
  for (const paragraph of text.split('\n')) {
    if (!paragraph) {
      out.push('')
      continue
    }
    let current = ''
    for (const char of Array.from(paragraph)) {
      const next = current + char
      if (ctx.measureText(next).width > maxWidth && current) {
        out.push(current)
        current = char
      } else {
        current = next
      }
    }
    if (current) out.push(current)
  }
  return out
}

async function buildShareImage(): Promise<Blob | null> {
  const width = 1080
  const posterHeight = 760
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  if (!ctx) return null

  const text = editingText.value || buildCopyText()
  ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif'
  const contentWidth = width - 96
  const lines = wrapLines(ctx, text, contentWidth)
  const lineHeight = 46
  const textHeight = lines.length * lineHeight
  const height = posterHeight + Math.max(260, textHeight + 96)
  canvas.width = width
  canvas.height = height

  ctx.fillStyle = '#0b1526'
  ctx.fillRect(0, 0, width, height)

  if (fullPosterUrl.value) {
    try {
      const img = await loadImage(fullPosterUrl.value)
      const scale = Math.max(width / img.width, posterHeight / img.height)
      const drawWidth = img.width * scale
      const drawHeight = img.height * scale
      ctx.drawImage(img, (width - drawWidth) / 2, (posterHeight - drawHeight) / 2, drawWidth, drawHeight)
      const gradient = ctx.createLinearGradient(0, posterHeight - 180, 0, posterHeight)
      gradient.addColorStop(0, 'rgba(11, 21, 38, 0)')
      gradient.addColorStop(1, '#0b1526')
      ctx.fillStyle = gradient
      ctx.fillRect(0, posterHeight - 180, width, 180)
    } catch {
      ctx.fillStyle = '#13233c'
      ctx.fillRect(0, 0, width, posterHeight)
      ctx.fillStyle = '#64748b'
      ctx.font = '32px "PingFang SC", "Microsoft YaHei", sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('海报加载失败', width / 2, posterHeight / 2)
      ctx.textAlign = 'left'
    }
  } else {
    ctx.fillStyle = '#13233c'
    ctx.fillRect(0, 0, width, posterHeight)
  }

  ctx.fillStyle = '#f8fafc'
  ctx.font = '30px "PingFang SC", "Microsoft YaHei", sans-serif'
  let y = posterHeight + 64
  for (const line of lines) {
    if (line) ctx.fillText(line, 48, y)
    y += lineHeight
  }

  return await new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), 'image/png'))
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

async function copyImage() {
  const blob = await buildShareImage()
  if (!blob) {
    ElMessage.error('图片生成失败')
    return
  }
  const canClipboard = typeof window !== 'undefined' && window.isSecureContext && 'ClipboardItem' in window && navigator.clipboard?.write
  if (canClipboard) {
    try {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      ElMessage.success('图片已复制')
      return
    } catch {
      /* fallthrough */
    }
  }
  downloadBlob(blob, `${draft.title || draft.taskname || '分享'}-分享卡.png`)
  ElMessage.warning('当前页面不是 HTTPS，浏览器不允许直接复制图片，已改为下载')
}

async function downloadImage() {
  const blob = await buildShareImage()
  if (!blob) {
    ElMessage.error('图片生成失败')
    return
  }
  downloadBlob(blob, `${draft.title || draft.taskname || '分享'}-分享卡.png`)
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
        <div class="section-header__desc">修改内容后复制文案或分享卡，全部在本地完成，不上传服务器。</div>
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
          <el-button @click="copyImage">复制图片</el-button>
          <el-button text @click="downloadImage">下载分享卡</el-button>
        </div>
      </section>
    </div>
  </div>
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
  border-radius: 14px;
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
</style>
