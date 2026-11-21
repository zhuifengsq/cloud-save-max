<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import WpsPluginSettings from '@/components/search/WpsPluginSettings.vue'

import {
  createApiSearchSource,
  deleteApiSearchSource,
  fetchApiSearchSources,
  fetchPythonSearchPlugins,
  patchApiSearchSource,
  patchPythonSearchPlugin,
  testApiSearchSource,
  testPythonSearchPlugin,
  uploadPythonSearchPlugin,
} from '@/api/resourceSearch'
import { PLUGIN_READ, PLUGIN_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { ApiSearchSource, ApiSearchSourceInput, PythonSearchPlugin, TaskSuggestionItem } from '@/types/resourceSearch'
import { formatDateTime } from '@/utils/capacity'

const auth = useAuthStore()
const canRead = computed(() => auth.permissions.includes(PLUGIN_READ) && auth.roles.includes('admin'))
const canWrite = computed(() => auth.permissions.includes(PLUGIN_WRITE) && auth.roles.includes('admin'))
const canUpload = computed(() => canWrite.value && auth.roles.includes('admin'))
const activeTab = ref('api')
const apiSources = ref<ApiSearchSource[]>([])
const plugins = ref<PythonSearchPlugin[]>([])
const loading = ref(false)
const busyKey = ref('')
const uploadInput = ref<HTMLInputElement | null>(null)

function hasWpsSettings(item: PythonSearchPlugin) {
  return item.configurable && (item.key === 'wps' || item.key === 'wps_movie')
}

const editor = reactive({
  visible: false,
  editingId: null as string | number | null,
  saving: false,
  name: '',
  enabled: true,
  method: 'GET' as 'GET' | 'POST',
  url: '',
  headers: '{}',
  query_params: '{}',
  body: '',
  response_rule: '',
  legacy: false,
  items_path: 'data.list',
  title_path: 'title',
  url_path: 'url',
  password_path: '',
  datetime_path: '',
})

const test = reactive({
  visible: false,
  kind: 'api' as 'api' | 'plugin',
  key: '' as string | number,
  title: '',
  keyword: '',
  loading: false,
  message: '',
  results: [] as TaskSuggestionItem[],
})

function errorText(error: any) {
  return String(error?.response?.data?.message || error?.message || '操作失败')
}

async function refresh() {
  if (!canRead.value) return
  loading.value = true
  try {
    const [apiRows, pluginRows] = await Promise.all([fetchApiSearchSources(), fetchPythonSearchPlugins()])
    apiSources.value = apiRows
    plugins.value = pluginRows
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

function asJson(value: Record<string, unknown> | null | undefined) {
  return JSON.stringify(value || {}, null, 2)
}

function openApiEditor(item?: ApiSearchSource) {
  editor.editingId = item?.id ?? null
  editor.name = item?.name || ''
  editor.enabled = item?.enabled ?? true
  editor.method = item?.method || 'GET'
  editor.url = item?.url || ''
  editor.headers = asJson(item?.headers)
  editor.query_params = asJson(item?.query_params)
  editor.body = item?.body && Object.keys(item.body).length ? asJson(item.body) : ''
  editor.response_rule = item?.response_rule || ''
  editor.legacy = Boolean(item && !item.response_rule)
  editor.items_path = item?.items_path || 'data.list'
  editor.title_path = item?.title_path || 'title'
  editor.url_path = item?.url_path || 'url'
  editor.password_path = item?.password_path || ''
  editor.datetime_path = item?.datetime_path || ''
  editor.visible = true
}

function parseJsonObject(label: string, text: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(text.trim() || '{}')
  } catch {
    throw new Error(`${label}不是有效的 JSON`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`)
  }
  return parsed as Record<string, unknown>
}

function formatJsonField(field: 'body' | 'headers' | 'query_params', label: string) {
  try {
    editor[field] = JSON.stringify(parseJsonObject(label, editor[field]), null, 2)
    ElMessage.success('JSON 已格式化')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function saveApi() {
  if (!canWrite.value) return
  editor.saving = true
  try {
    if (!editor.name.trim() || !editor.url.trim() || (!editor.response_rule.trim() && !editor.legacy)) {
      throw new Error('请填写接口名称、URL 和 JMESPath 响应解析规则')
    }
    const payload: ApiSearchSourceInput = {
      name: editor.name.trim(),
      enabled: editor.enabled,
      method: editor.method,
      url: editor.url.trim(),
      headers: parseJsonObject('请求头', editor.headers) as Record<string, string>,
      query_params: parseJsonObject('查询参数', editor.query_params),
      body: parseJsonObject('请求体', editor.body),
      response_rule: editor.response_rule.trim(),
      items_path: editor.items_path.trim(),
      title_path: editor.title_path.trim(),
      url_path: editor.url_path.trim(),
      password_path: editor.password_path.trim(),
      datetime_path: editor.datetime_path.trim(),
    }
    if (editor.editingId === null) await createApiSearchSource(payload)
    else await patchApiSearchSource(editor.editingId, payload)
    editor.visible = false
    ElMessage.success('API 搜索源已保存')
    await refresh()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    editor.saving = false
  }
}

async function toggleApi(item: ApiSearchSource, enabled: boolean) {
  busyKey.value = `api:${item.id}`
  try {
    await patchApiSearchSource(item.id, { enabled })
    await refresh()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busyKey.value = ''
  }
}

async function removeApi(item: ApiSearchSource) {
  try {
    await ElMessageBox.confirm(`删除搜索源“${item.name}”？`, '确认删除', { type: 'warning' })
    await deleteApiSearchSource(item.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorText(error))
  }
}

async function togglePlugin(item: PythonSearchPlugin, enabled: boolean) {
  busyKey.value = `plugin:${item.key}`
  try {
    await patchPythonSearchPlugin(item.key, enabled)
    await refresh()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busyKey.value = ''
  }
}

function openTest(kind: 'api' | 'plugin', key: string | number, title: string) {
  test.kind = kind
  test.key = key
  test.title = title
  test.keyword = ''
  test.message = ''
  test.results = []
  test.visible = true
}

async function runTest() {
  if (!test.keyword.trim()) return ElMessage.warning('请输入测试关键词')
  test.loading = true
  test.results = []
  test.message = ''
  try {
    const result = test.kind === 'api'
      ? await testApiSearchSource(test.key, test.keyword.trim())
      : await testPythonSearchPlugin(String(test.key), test.keyword.trim())
    test.results = Array.isArray(result) ? result : (result.results || result.items || result.data || [])
    test.message = Array.isArray(result) ? '' : (result.error || result.message || '')
    if (test.kind === 'plugin') await refresh()
  } catch (error) {
    test.message = errorText(error)
  } finally {
    test.loading = false
  }
}

async function uploadPlugin(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!/^[a-z][a-z0-9_]{0,24}_plugin\.py$/.test(file.name)) return ElMessage.warning('文件名需为 mysearch_plugin.py 这样的格式')
  if (file.size > 256 * 1024) return ElMessage.warning('插件文件不能超过 256 KiB')
  busyKey.value = 'upload'
  try {
    await uploadPythonSearchPlugin(file)
    ElMessage.success('插件已上传')
    await refresh()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    busyKey.value = ''
  }
}

onMounted(() => { if (canRead.value) refresh() })
</script>

<template>
  <section class="search-sources-page">
    <div class="page-heading">
      <div>
        <div class="page-heading__eyebrow">SEARCH SOURCES</div>
        <h1>搜索源管理</h1>
        <p>搜索结果会出现在新增追剧任务时，选择链接后再保存任务。</p>
      </div>
      <el-button :loading="loading" @click="refresh">刷新全部</el-button>
    </div>

    <div class="source-overview">
      <div class="source-overview__item"><span>API 搜索源</span><strong>{{ apiSources.length }}</strong></div>
      <div class="source-overview__item"><span>Python 插件</span><strong>{{ plugins.length }}</strong></div>
      <div class="source-overview__item"><span>已启用</span><strong>{{ apiSources.filter(item => item.enabled).length + plugins.filter(item => item.enabled).length }}</strong></div>
    </div>

    <el-alert v-if="!canRead" title="搜索源配置仅限管理员查看" type="warning" :closable="false" />
    <el-tabs v-else v-model="activeTab" class="source-tabs">
      <el-tab-pane label="API 搜索源" name="api">
        <div class="tab-heading">
          <p>配置返回 JSON 的搜索接口。URL 或请求体中用 <code>[[keyword]]</code> 表示搜索关键词。</p>
          <el-button type="primary" :disabled="!canWrite" @click="openApiEditor()">新增 API 源</el-button>
        </div>
        <el-empty v-if="!loading && !apiSources.length" description="还没有 API 搜索源" />
        <div v-else class="source-grid">
          <article v-for="item in apiSources" :key="item.id" class="glass-panel source-card">
            <header class="source-card__header">
              <hgroup class="source-card__heading">
                <h3>{{ item.name }}</h3>
                <p>{{ item.method }}</p>
              </hgroup>
              <el-switch
                :aria-label="`启用 ${item.name}`"
                :model-value="item.enabled"
                :disabled="!canWrite || busyKey === `api:${item.id}`"
                @change="toggleApi(item, Boolean($event))"
              />
            </header>

            <div>
              <el-tag :type="item.enabled ? 'success' : 'info'" effect="plain" round size="small">
                {{ item.enabled ? '已启用' : '已停用' }}
              </el-tag>
            </div>

            <div class="source-card__body">
              <div class="source-card__label">接口 URL</div>
              <p class="source-card__summary">{{ item.url }}</p>
            </div>

            <footer class="source-card__actions">
              <el-button type="primary" text bg @click="openTest('api', item.id, item.name)">测试搜索</el-button>
              <el-button type="primary" text bg :disabled="!canWrite" @click="openApiEditor(item)">编辑</el-button>
              <el-button type="danger" text bg :disabled="!canWrite" @click="removeApi(item)">删除</el-button>
            </footer>
          </article>
        </div>
      </el-tab-pane>

      <el-tab-pane label="Python 搜索插件" name="plugin">
        <div class="tab-heading">
          <p>上传名为 <code>&lt;key&gt;_plugin.py</code> 的 UTF-8 文件，例如 <code>mysearch_plugin.py</code>。插件代码会在服务器运行，只允许管理员上传。</p>
          <div>
            <input ref="uploadInput" type="file" accept=".py" class="file-input" @change="uploadPlugin" />
            <el-button type="primary" :loading="busyKey === 'upload'" :disabled="!canUpload" @click="uploadInput?.click()">上传 Python 插件</el-button>
          </div>
        </div>
        <details class="plugin-help">
          <summary>查看插件格式</summary>
          <pre>from app.extensions.search_plugins.base import BaseSearchPlugin

class MySearchPlugin(BaseSearchPlugin):
    key = "mysearch"
    name = "我的搜索"

    def search(self, keyword):
        # 返回真实搜索结果；网络请求请设置超时
        return []  # 实际结果格式：{"taskname": "片名", "shareurl": "真实分享链接"}</pre>
          <p>文件名中的 key 必须与类的 key 一致。上传后在列表中启用，再用“测试搜索”检查结果。</p>
        </details>
        <el-empty v-if="!loading && !plugins.length" description="还没有 Python 搜索插件" />
        <div v-else class="source-grid">
          <article v-for="item in plugins" :key="item.key" class="glass-panel source-card">
            <header class="source-card__header">
              <hgroup class="source-card__heading">
                <h3>{{ item.name || item.key }}</h3>
                <p>{{ item.key }}</p>
              </hgroup>
              <el-switch
                :aria-label="`启用 ${item.name || item.key}`"
                :model-value="item.enabled"
                :disabled="!canWrite || item.status !== 'ready' || busyKey === `plugin:${item.key}`"
                @change="togglePlugin(item, Boolean($event))"
              />
            </header>

            <div>
              <el-tag
                :type="item.status !== 'ready' ? 'danger' : item.enabled ? 'success' : 'info'"
                effect="plain" round size="small"
              >
                {{ item.status !== 'ready' ? '异常' : item.enabled ? '已启用' : '已停用' }}
              </el-tag>
            </div>

            <div class="source-card__body">
              <div class="source-card__label">插件说明</div>
              <p class="source-card__summary">{{ item.description || '未填写插件说明' }}</p>
              <p v-if="item.error" class="source-card__error">{{ item.error }}</p>
              <p v-if="hasWpsSettings(item)" class="source-card__cache">
                <template v-if="item.cache?.cached">
                  {{ item.cache.expired ? '缓存已过期' : '已缓存' }} · {{ item.cache.count }} 条链接 · {{ formatDateTime(item.cache.updated_at) }}
                </template>
                <template v-else>暂无缓存，首次搜索时读取文档</template>
              </p>
            </div>

            <footer class="source-card__actions">
              <el-button type="primary" text bg @click="openTest('plugin', item.key, item.name || item.key)">测试搜索</el-button>
              <WpsPluginSettings v-if="hasWpsSettings(item)" :plugin="item" :can-write="canWrite" @changed="refresh" />
            </footer>
          </article>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="editor.visible"
      :title="editor.editingId === null ? '新增 API 搜索源' : '修改 API 搜索源'"
      width="min(920px, 96vw)"
      class="api-editor-dialog"
      align-center
      append-to-body
      destroy-on-close
    >
      <el-form label-position="top" class="editor-form">
        <div class="editor-card">
          <div class="editor-top-grid">
            <el-form-item label="接口名称 *"><el-input v-model="editor.name" placeholder="例如：盘搜" /></el-form-item>
            <el-form-item label="请求方法 *"><el-select v-model="editor.method"><el-option label="GET" value="GET" /><el-option label="POST" value="POST" /></el-select></el-form-item>
            <el-form-item label="启用状态 *"><el-select v-model="editor.enabled"><el-option label="启用" :value="true" /><el-option label="禁用" :value="false" /></el-select></el-form-item>
          </div>
          <el-form-item>
            <template #label>接口 URL * <span class="editor-label-hint">（在 URL 或请求体中嵌入 [[keyword]] 作为关键词占位符）</span></template>
            <el-input v-model="editor.url" placeholder="https://example.com/api/search?kw=[[keyword]]" />
          </el-form-item>
        </div>

        <div class="editor-card">
          <div class="editor-card-heading"><strong>请求体配置 (JSON)</strong><el-button size="small" @click="formatJsonField('body', '请求体')">格式化</el-button></div>
          <el-input v-model="editor.body" type="textarea" :rows="4" placeholder='POST 请求可留空或配置 JSON，例如：{"text":"[[keyword]]"}' />
          <p class="editor-help">GET 请求填写时会作为查询参数，POST 请求填写时会发送 JSON 请求体。</p>
        </div>

        <div class="editor-card">
          <div class="editor-card-heading"><strong>请求头配置 (JSON，可选)</strong><el-button size="small" @click="formatJsonField('headers', '请求头')">格式化</el-button></div>
          <el-input v-model="editor.headers" type="textarea" :rows="4" placeholder='{"Authorization":"Bearer 你的令牌"}' />
          <p class="editor-help">用于需要 Authorization 等请求头的接口；留空则不添加。</p>
        </div>

        <div class="editor-card">
          <div class="editor-card-heading">
            <strong>响应解析规则 (JMESPath) *</strong>
            <a href="https://jmespath.org/tutorial.html" target="_blank" rel="noopener noreferrer">JMESPath 语法教程 ↗</a>
          </div>
          <el-input v-model="editor.response_rule" type="textarea" :rows="3" placeholder="例如：data[*].[name, url]" />
          <p class="editor-help">规则须返回列表。可用 <code>data[*].[name, url]</code>，或 <code>data[*].{title:name,url:share}</code>。</p>
        </div>

        <details v-if="editor.legacy" class="editor-card legacy-card" open>
          <summary>旧版字段映射（当前配置仍按这些字段运行；填写上方 JMESPath 后会改用新规则）</summary>
          <div class="mapping-grid">
            <el-form-item label="结果列表路径"><el-input v-model="editor.items_path" /></el-form-item>
            <el-form-item label="标题字段"><el-input v-model="editor.title_path" /></el-form-item>
            <el-form-item label="链接字段"><el-input v-model="editor.url_path" /></el-form-item>
            <el-form-item label="提取码字段"><el-input v-model="editor.password_path" /></el-form-item>
            <el-form-item label="发布时间字段"><el-input v-model="editor.datetime_path" /></el-form-item>
          </div>
        </details>
        <details v-if="editor.legacy || editor.query_params !== '{}'" class="editor-card legacy-card">
          <summary>额外查询参数（兼容原有配置）</summary>
          <el-input v-model="editor.query_params" type="textarea" :rows="3" placeholder='{"q":"[[keyword]]"}' />
        </details>
      </el-form>
      <template #footer><el-button @click="editor.visible = false">取消</el-button><el-button type="primary" :loading="editor.saving" @click="saveApi">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="test.visible" :title="`测试搜索：${test.title}`" width="min(760px, 96vw)" append-to-body>
      <div class="test-bar"><el-input v-model="test.keyword" placeholder="输入影视名称" @keyup.enter="runTest" /><el-button type="primary" :loading="test.loading" @click="runTest">搜索</el-button></div>
      <p v-if="test.message" class="test-message">{{ test.message }}</p>
      <el-empty v-if="!test.loading && !test.results.length" description="暂无搜索结果" />
      <div v-else class="test-results">
        <div v-for="(item, index) in test.results" :key="`${item.shareurl}-${index}`" class="test-result">
          <div class="test-result__head">
            <strong>{{ item.taskname }}</strong>
            <span v-if="item.source" class="test-result__source">{{ item.source }}</span>
          </div>
          <div class="test-result__link">{{ item.shareurl }}</div>
        </div>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.search-sources-page { width: 100%; max-width: 1380px; margin: 0 auto; display: grid; gap: 20px; }
.page-heading, .tab-heading, .test-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-heading { padding: 0; }
.page-heading__eyebrow { display: none; margin-bottom: 6px; color: var(--el-color-primary); font-size: 11px; font-weight: 750; letter-spacing: .18em; }
.page-heading h1 { margin: 0 0 6px; font-size: 22px; font-weight: 700; line-height: 1.3; letter-spacing: -.02em; }
.page-heading p, .tab-heading p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.6; }
.source-overview { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.source-overview__item { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border: 1px solid var(--panel-border); border-radius: var(--panel-radius); background: var(--panel-bg); box-shadow: var(--panel-shadow); color: var(--el-text-color-secondary); font-size: 13px; }
.source-overview__item strong { color: var(--el-text-color-primary); font-size: 24px; line-height: 1; }
.source-tabs { padding: 20px 24px 28px; border: 1px solid var(--panel-border); border-radius: var(--panel-radius); background: var(--panel-bg); box-shadow: var(--panel-shadow); }
.source-tabs :deep(.el-tabs__item) { height: 48px; font-size: 14px; font-weight: 650; }
.source-tabs :deep(.el-tabs__header) { margin-bottom: 22px; }
.tab-heading { margin: 0 0 20px; padding: 2px 0 20px; border-bottom: 1px solid var(--panel-border); }
.tab-heading p { max-width: 75ch; font-size: 13px; }
.tab-heading code { padding: 2px 5px; border-radius: 5px; background: var(--el-fill-color-light); color: var(--el-color-primary); }
.file-input { display: none; }
.plugin-help { margin: 0 0 18px; color: var(--el-text-color-secondary); }
.plugin-help summary { cursor: pointer; color: var(--el-color-primary); }
.plugin-help pre { margin: 10px 0; padding: 12px; overflow-x: auto; border-radius: 8px; background: var(--el-fill-color-light); color: var(--el-text-color-primary); }
.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
  gap: 16px;
}
.source-card {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
  min-height: 260px;
  padding: 22px;
}
.source-card__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.source-card__header .el-switch { flex-shrink: 0; }
.source-card__heading { min-width: 0; overflow-wrap: anywhere; }
.source-card__heading h3 { margin: 0; font-size: 18px; line-height: 1.35; }
.source-card__heading p { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
.source-card__body { display: flex; flex: 1; flex-direction: column; gap: 8px; overflow-wrap: anywhere; }
.source-card__label { color: var(--el-text-color-secondary); font-size: 12px; font-weight: 600; }
.source-card__summary,
.source-card__error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
  font-size: 13px;
  line-height: 1.5;
}
.source-card__error {
  border-color: var(--el-color-danger-light-7);
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
.source-card__cache { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.source-card__actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.source-card__actions > .el-button { margin: 0; }
.mapping-help { margin: 0 0 14px; color: var(--el-text-color-secondary); line-height: 1.6; }
:global(.api-editor-dialog.el-dialog) {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 24px);
  max-height: calc(100dvh - 24px);
}
:global(.api-editor-dialog .el-dialog__body) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior-y: contain;
}
.editor-form { padding: 4px 8px 4px 0; display: grid; gap: 18px; }
.editor-card { padding: 20px; border: 1px solid var(--el-border-color-lighter); border-radius: var(--panel-radius); background: var(--el-fill-color-extra-light); }
.editor-card :deep(.el-form-item) { margin-bottom: 14px; }
.editor-card :deep(.el-form-item__label) { font-weight: 700; }
.editor-card :deep(.el-input__wrapper), .editor-card :deep(.el-select__wrapper) { min-height: 46px; border-radius: 13px; }
.editor-card :deep(.el-textarea__inner) { border-radius: 13px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.editor-top-grid { display: grid; grid-template-columns: 2.1fr 1fr 1fr; gap: 18px; }
.editor-top-grid :deep(.el-select) { width: 100%; }
.editor-label-hint { color: var(--el-text-color-placeholder); font-weight: 400; }
.editor-card-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
.editor-card-heading strong { font-size: 15px; }
.editor-card-heading a { color: var(--el-color-primary); font-size: 13px; text-decoration: none; }
.editor-help { margin: 10px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; }
.legacy-card summary { cursor: pointer; color: var(--el-text-color-secondary); font-size: 13px; }
.legacy-card .mapping-grid { margin-top: 18px; }
.mapping-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.test-bar .el-input { flex: 1; }
.test-message { color: var(--el-text-color-secondary); }
.test-results { max-height: 48vh; overflow-y: auto; display: grid; gap: 10px; }
.test-result { padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; }
.test-result__head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.test-result__source { padding: 1px 8px; border-radius: 999px; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); font-size: 11px; line-height: 18px; }
.test-result__link { margin-top: 6px; overflow-wrap: anywhere; color: var(--el-color-primary); }
@media (max-width: 700px) {
  .page-heading, .tab-heading { align-items: flex-start; }
  .page-heading, .tab-heading { flex-direction: column; }
  .page-heading h1 { font-size: 19px; }
  .source-overview { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
  .source-overview__item { display: block; padding: 12px; font-size: 11px; }
  .source-overview__item strong { display: block; margin-top: 7px; font-size: 21px; }
  .source-card { min-height: auto; padding: 18px; }
  .mapping-grid { grid-template-columns: 1fr; }
  .editor-top-grid { grid-template-columns: 1fr; gap: 0; }
  .editor-card { padding: 14px; }
  .editor-label-hint { display: block; }
  .source-tabs { padding: 14px; }
}
</style>
