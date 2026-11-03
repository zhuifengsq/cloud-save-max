<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'
import {
  clipboardHtmlToText, DEFAULT_HOT_PREFIX, DEFAULT_HOT_SUFFIX, DEFAULT_QUARK_SUFFIX,
  extractTable, filterQuark, generateHotUpdates, organizeDualDisk, type ExtractedLink,
} from '@/utils/linkExtractor'

type Tab = 'table' | 'quark' | 'dual' | 'hot'
const tabs: { key: Tab; label: string; action: string; placeholder: string }[] = [
  { key: 'table', label: '表格提取', action: '解析提取', placeholder: '粘贴剧名和分享链接，提取为两列表格' },
  { key: 'quark', label: '夸克过滤', action: '仅保留夸克', placeholder: '粘贴包含多种网盘链接的内容，保留剧名和夸克链接' },
  { key: 'dual', label: '百度/夸克整理', action: '整理格式', placeholder: '粘贴原始内容，支持普通网址、Markdown 和富文本超链接' },
  { key: 'hot', label: '热播更新', action: '生成更新文案', placeholder: '粘贴剧名和更新集数，例如：尚公主 (2026) 【更23集】' },
]
const activeTab = ref<Tab>('table')
const tabInfo = computed(() => tabs.find((tab) => tab.key === activeTab.value)!)
const empty = () => ({ input: '', output: '', processed: false, status: '', hasMatches: false })
const states = reactive({ table: empty(), quark: empty(), dual: empty(), hot: empty() })
const current = computed(() => states[activeTab.value])
const rows = ref<ExtractedLink[]>([])
const quarkSuffix = ref(DEFAULT_QUARK_SUFFIX)
const hotPrefix = ref(DEFAULT_HOT_PREFIX)
const hotSuffix = ref(DEFAULT_HOT_SUFFIX)
const exporting = ref(false)

function process() {
  const key = activeTab.value
  const state = states[key]
  if (key === 'table') {
    rows.value = extractTable(state.input)
    state.hasMatches = rows.value.length > 0
    state.status = `已提取 ${rows.value.length} 条链接`
  } else if (key === 'quark') {
    state.output = filterQuark(state.input, quarkSuffix.value)
    state.hasMatches = Boolean(filterQuark(state.input, ''))
    state.status = state.hasMatches ? '已保留夸克链接' : '没有识别到带剧名的夸克链接'
  } else if (key === 'dual') {
    const result = organizeDualDisk(state.input)
    state.output = result.text
    state.hasMatches = result.baiduCount + result.quarkCount > 0
    state.status = `已识别 ${result.baiduCount} 个百度链接、${result.quarkCount} 个夸克链接`
  } else {
    const result = generateHotUpdates(state.input, hotPrefix.value, hotSuffix.value)
    state.output = result.text
    state.hasMatches = result.count > 0
    state.status = result.count ? `已生成 ${result.count} 部影视的更新文案` : '没有识别到包含集数的影视标题'
  }
  state.processed = true
}

function clear() {
  Object.assign(current.value, empty())
  if (activeTab.value === 'table') rows.value = []
}

function paste(event: ClipboardEvent) {
  if (activeTab.value !== 'dual' && activeTab.value !== 'hot') return
  const target = event.target
  if (!(target instanceof HTMLTextAreaElement) || !event.clipboardData) return
  const text = clipboardHtmlToText(event.clipboardData.getData('text/html')) || event.clipboardData.getData('text/plain')
  if (!text) return
  event.preventDefault()
  const start = target.selectionStart
  const end = target.selectionEnd
  current.value.input = current.value.input.slice(0, start) + text + current.value.input.slice(end)
  process()
}

async function copyResult() {
  const text = activeTab.value === 'table'
    ? rows.value.map((row) => `${row.title}\t${row.url}`).join('\n')
    : current.value.output
  if (await copyText(text)) ElMessage.success(activeTab.value === 'table' ? '表格已复制，可粘贴到 Excel 或 WPS' : '已复制')
  else ElMessage.warning('复制失败，请手动选中结果复制')
}

async function exportExcel() {
  if (!rows.value.length || exporting.value) return
  const data = rows.value.map((row) => [row.title, row.url])
  exporting.value = true
  try {
    const XLSX = await import('xlsx')
    // Preserve the original file's format: two columns, no header, starting at A2.
    const sheet = XLSX.utils.aoa_to_sheet([])
    XLSX.utils.sheet_add_aoa(sheet, data, { origin: 'A2' })
    sheet['!cols'] = [{ wch: 30 }, { wch: 60 }]
    const workbook = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(workbook, sheet, '提取结果')
    const now = new Date()
    XLSX.writeFile(workbook, `${now.getMonth() + 1}月${now.getDate()}日.xlsx`)
    ElMessage.success('Excel 已导出')
  } catch {
    ElMessage.error('导出失败，请重试')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="shell-page link-extractor">
    <div class="section-header">
      <div class="section-header__title">
        <h2>链接整理</h2>
        <div class="section-header__desc">提取影视链接、整理网盘内容，生成更新文案。</div>
      </div>
      <el-tag type="info" effect="plain">本地处理</el-tag>
    </div>

    <section class="glass-panel tool-panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane v-for="tab in tabs" :key="tab.key" :label="tab.label" :name="tab.key" />
      </el-tabs>
      <div class="tool-content">
        <div class="tool-field">
          <label for="link-source">原始内容</label>
          <el-input id="link-source" v-model="current.input" type="textarea" :rows="9" :placeholder="tabInfo.placeholder" @paste="paste" />
        </div>
        <div v-if="activeTab === 'quark'" class="tool-field">
          <label for="quark-suffix">输出后缀</label>
          <el-input id="quark-suffix" v-model="quarkSuffix" type="textarea" :rows="4" placeholder="可选，附加在结果末尾" />
        </div>
        <div v-if="activeTab === 'hot'" class="affix-grid">
          <div class="tool-field">
            <label for="hot-prefix">输出前缀</label>
            <el-input id="hot-prefix" v-model="hotPrefix" type="textarea" :rows="3" placeholder="可选" />
          </div>
          <div class="tool-field">
            <label for="hot-suffix">输出后缀</label>
            <el-input id="hot-suffix" v-model="hotSuffix" type="textarea" :rows="3" placeholder="可选" />
          </div>
        </div>
        <div class="tool-actions">
          <el-button @click="clear">清空</el-button>
          <el-button type="primary" :disabled="!current.input.trim()" @click="process">{{ tabInfo.action }}</el-button>
        </div>

        <section v-if="current.processed" class="tool-result" aria-label="整理结果">
          <div class="result-heading">
            <div class="result-title">
              <h3>{{ activeTab === 'table' ? '提取结果' : '整理结果' }}</h3>
              <span role="status">{{ current.status }}</span>
            </div>
            <div class="result-actions">
              <el-button :disabled="activeTab === 'table' ? !rows.length : !current.output" @click="copyResult">{{ activeTab === 'table' ? '复制表格' : '复制文本' }}</el-button>
              <el-button v-if="activeTab === 'table'" type="primary" plain :disabled="!rows.length" :loading="exporting" @click="exportExcel">导出 Excel</el-button>
            </div>
          </div>
          <template v-if="activeTab === 'table'">
            <el-table :data="rows" max-height="440" empty-text="未识别到链接，请检查粘贴内容" stripe>
              <el-table-column prop="title" label="影视名称" min-width="150" />
              <el-table-column label="下载链接" min-width="250">
                <template #default="scope">
                  <a class="result-link" :href="scope.row.url" target="_blank" rel="noopener noreferrer">{{ scope.row.url }}</a>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-input v-else :model-value="current.output" type="textarea" :rows="9" readonly aria-label="整理结果文本" />
        </section>
        <div v-else class="result-placeholder">{{ activeTab === 'table' ? '提取后可预览、复制或导出 Excel' : '整理后的内容会显示在这里' }}</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.link-extractor { min-width: 0; }
.tool-panel { padding: 8px 24px 24px; min-width: 0; }
.tool-content { display: grid; gap: 20px; padding-top: 8px; min-width: 0; }
.tool-field { display: grid; gap: 10px; min-width: 0; }
.tool-field label { color: var(--ink-2); font-size: 14px; font-weight: 600; }
.tool-field :deep(textarea), .tool-result :deep(textarea) { line-height: 1.7; }
.affix-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.tool-actions, .result-actions { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.tool-actions .el-button, .result-actions .el-button { margin-left: 0; }
.tool-result { min-width: 0; border-top: 1px solid var(--line); padding-top: 24px; display: grid; gap: 16px; }
.result-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.result-title { display: grid; gap: 6px; }
.result-title h3 { margin: 0; font-size: 16px; }
.result-title span { color: var(--ink-3); font-size: 13px; }
.result-link { color: var(--brand); overflow-wrap: anywhere; }
.result-link:hover { text-decoration: underline; }
.result-placeholder { padding: 26px 12px; border: 1px dashed var(--line-strong); border-radius: var(--control-radius); text-align: center; color: var(--ink-3); font-size: 14px; }
@media (max-width: 640px) {
  .tool-panel { padding: 4px 14px 18px; }
  .affix-grid { grid-template-columns: 1fr; }
  .result-actions { justify-content: flex-start; }
}
</style>
