<script setup lang="ts">
import { CircleCheck, Connection, Cpu } from '@element-plus/icons-vue'
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
import { isImeComposing } from '@/utils/ime'

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
    await ElMessageBox.confirm(`删除搜索源“${item.name}”？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
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
  <section class="shell-page search-sources-page">
    <div class="section-header">
      <div class="section-header__title">
        <h1>搜索源管理</h1>
        <div class="section-header__desc">搜索结果会出现在新增追剧任务时，选择链接后再保存任务。</div>
      </div>
      <div class="toolbar__right">
        <el-button type="primary" :loading="loading" @click="refresh">刷新全部</el-button>
      </div>
    </div>

    <section class="metric-strip source-overview">
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">API 搜索源</div>
          <span class="metric-tile__icon metric-tile__icon--brand" aria-hidden="true">
            <el-icon><Connection /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ apiSources.length }}</div>
        <div class="metric-tile__hint">自定义 JSON 接口</div>
      </div>
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">Python 插件</div>
          <span class="metric-tile__icon metric-tile__icon--violet" aria-hidden="true">
            <el-icon><Cpu /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ plugins.length }}</div>
        <div class="metric-tile__hint">服务端脚本搜索源</div>
      </div>
      <div class="glass-panel metric-tile">
        <div class="metric-tile__head">
          <div class="metric-tile__label">已启用</div>
          <span class="metric-tile__icon metric-tile__icon--success" aria-hidden="true">
            <el-icon><CircleCheck /></el-icon>
          </span>
        </div>
        <div class="metric-tile__value">{{ apiSources.filter(item => item.enabled).length + plugins.filter(item => item.enabled).length }}</div>
        <div class="metric-tile__hint">当前参与搜索的源总数</div>
      </div>
    </section>

    <el-alert v-if="!canRead" title="搜索源配置仅限管理员查看" type="warning" :closable="false" />
    <el-tabs v-else v-model="activeTab" class="glass-panel source-tabs">
      <el-tab-pane label="API 搜索源" name="api">
        <div class="tab-heading">
          <p>配置返回 JSON 的搜索接口。URL 或请求体中用 <code>[[keyword]]</code> 表示搜索关键词。</p>
          <el-button type="primary" :disabled="!canWrite" @click="openApiEditor()">新增 API 源</el-button>
        </div>
        <div v-if="loading && !apiSources.length" class="source-grid" aria-hidden="true">
          <div v-for="i in 3" :key="`api-sk-${i}`" class="glass-panel source-card">
            <div class="source-card__header">
              <span class="ui-skeleton" style="width: 48%; height: 22px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 40px; height: 20px"></span>
            </div>
            <span class="ui-skeleton ui-skeleton--pill" style="width: 60px; height: 22px"></span>
            <span class="ui-skeleton" style="width: 100%; height: 52px"></span>
          </div>
        </div>
        <div v-else-if="!apiSources.length" class="empty-state">
          <div class="empty-state__title">还没有 API 搜索源</div>
          <div class="empty-state__desc">新增 JSON 搜索接口后，即可在创建追剧任务时自动聚合结果。</div>
          <div class="empty-state__actions">
            <el-button type="primary" :disabled="!canWrite" @click="openApiEditor()">新增 API 源</el-button>
          </div>
        </div>
        <div v-else class="source-grid" v-loading="loading">
          <article v-for="item in apiSources" :key="item.id" class="glass-panel source-card">
            <header class="source-card__header">
              <hgroup class="source-card__heading">
                <h3>{{ item.name }}</h3>
                <div class="source-card__badges">
                  <el-tag :type="item.method === 'POST' ? 'warning' : 'primary'" effect="plain" size="small">
                    {{ item.method }}
                  </el-tag>
                  <el-tag :type="item.enabled ? 'success' : 'info'" effect="plain" round size="small">
                    {{ item.enabled ? '已启用' : '已停用' }}
                  </el-tag>
                </div>
              </hgroup>
              <el-switch
                :aria-label="`启用 ${item.name}`"
                :model-value="item.enabled"
                :disabled="!canWrite || busyKey === `api:${item.id}`"
                @change="toggleApi(item, Boolean($event))"
              />
            </header>

            <div class="source-card__body">
              <div class="source-card__label">接口 URL</div>
              <p class="source-card__summary source-card__summary--mono">{{ item.url }}</p>
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
        <div v-if="loading && !plugins.length" class="source-grid" aria-hidden="true">
          <div v-for="i in 3" :key="`plug-sk-${i}`" class="glass-panel source-card">
            <div class="source-card__header">
              <span class="ui-skeleton" style="width: 52%; height: 22px"></span>
              <span class="ui-skeleton ui-skeleton--pill" style="width: 40px; height: 20px"></span>
            </div>
            <span class="ui-skeleton ui-skeleton--pill" style="width: 60px; height: 22px"></span>
            <span class="ui-skeleton" style="width: 100%; height: 52px"></span>
          </div>
        </div>
        <div v-else-if="!plugins.length" class="empty-state">
          <div class="empty-state__title">还没有 Python 搜索插件</div>
          <div class="empty-state__desc">上传符合规范的 <code>_plugin.py</code> 脚本后，可在此启用并测试搜索。</div>
        </div>
        <div v-else class="source-grid" v-loading="loading">
          <article v-for="item in plugins" :key="item.key" class="glass-panel source-card">
            <header class="source-card__header">
              <hgroup class="source-card__heading">
                <h3>{{ item.name || item.key }}</h3>
                <div class="source-card__badges">
                  <code class="source-card__key">{{ item.key }}</code>
                  <el-tag
                    :type="item.status !== 'ready' ? 'danger' : item.enabled ? 'success' : 'info'"
                    effect="plain" round size="small"
                  >
                    {{ item.status !== 'ready' ? '异常' : item.enabled ? '已启用' : '已停用' }}
                  </el-tag>
                </div>
              </hgroup>
              <el-switch
                :aria-label="`启用 ${item.name || item.key}`"
                :model-value="item.enabled"
                :disabled="!canWrite || item.status !== 'ready' || busyKey === `plugin:${item.key}`"
                @change="togglePlugin(item, Boolean($event))"
              />
            </header>

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
      <div class="test-bar"><el-input v-model="test.keyword" placeholder="输入影视名称" @keydown.enter="!isImeComposing($event) && runTest()" /><el-button type="primary" :loading="test.loading" @click="runTest">搜索</el-button></div>
      <p v-if="test.message" class="test-message">{{ test.message }}</p>
      <div v-if="test.loading" class="test-results" aria-hidden="true">
        <div v-for="i in 3" :key="`test-sk-${i}`" class="test-result">
          <span class="ui-skeleton" style="width: 58%; height: 16px"></span>
          <span class="ui-skeleton" style="width: 92%; height: 13px; margin-top: 8px"></span>
        </div>
      </div>
      <div v-else-if="!test.results.length" class="empty-state empty-state--compact">
        <div class="empty-state__title">暂无搜索结果</div>
        <div class="empty-state__desc">输入影视关键词后点击搜索验证结果解析。</div>
      </div>
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
.search-sources-page { width: 100%; }
.tab-heading, .test-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tab-heading p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.6; }
.source-overview { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.source-tabs { padding: 18px 22px 24px; }
.source-tabs :deep(.el-tabs__item) { height: 46px; font-size: 14px; font-weight: 600; }
.source-tabs :deep(.el-tabs__header) { margin-bottom: 20px; }
.tab-heading { margin: 0 0 18px; padding: 2px 0 18px; border-bottom: 1px solid var(--panel-border); }
.tab-heading p { max-width: 75ch; font-size: 13px; }
.tab-heading code { padding: 2px 6px; border-radius: 5px; background: var(--surface-muted); color: var(--el-color-primary); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.file-input { display: none; }
.plugin-help { margin: 0 0 18px; color: var(--el-text-color-secondary); font-size: 13px; }
.plugin-help summary { cursor: pointer; color: var(--el-color-primary); font-weight: 500; }
.plugin-help pre { margin: 10px 0; padding: 14px; overflow-x: auto; border-radius: 10px; border: 1px solid var(--line); background: var(--surface-muted); color: var(--ink); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12.5px; line-height: 1.55; }
.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
  gap: 16px;
}
.source-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  min-height: 230px;
  padding: 20px;
}
.source-card__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.source-card__header .el-switch { flex-shrink: 0; }
.source-card__heading { min-width: 0; overflow-wrap: anywhere; }
.source-card__heading h3 { margin: 0; font-size: 17px; font-weight: 650; line-height: 1.35; color: var(--ink); }
.source-card__badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.source-card__key {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--surface-muted);
  border: 1px solid var(--line);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  color: var(--ink-2);
}
.source-card__body { display: flex; flex: 1; flex-direction: column; gap: 6px; overflow-wrap: anywhere; }
.source-card__label { color: var(--el-text-color-secondary); font-size: 12px; font-weight: 600; }
.source-card__summary,
.source-card__error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--control-radius);
  background: var(--surface-muted);
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink);
}
.source-card__summary--mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
}
.source-card__error {
  border-color: var(--el-color-danger-light-7);
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
.source-card__cache { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.source-card__actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; padding-top: 6px; border-top: 1px solid var(--line); }
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
.editor-card :deep(.el-input__wrapper), .editor-card :deep(.el-select__wrapper) { min-height: 46px; border-radius: var(--control-radius); }
.editor-card :deep(.el-textarea__inner) { border-radius: var(--control-radius); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
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
.test-results { max-height: 48vh; overflow-y: auto; display: grid; gap: 10px; margin-top: 12px; }
.test-result { padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-muted); }
.test-result__head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.test-result__source { padding: 1px 8px; border-radius: 999px; background: var(--card); border: 1px solid var(--line); color: var(--el-text-color-secondary); font-size: 11px; line-height: 18px; }
.test-result__link { margin-top: 6px; overflow-wrap: anywhere; color: var(--el-color-primary); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
@media (max-width: 700px) {
  .tab-heading { flex-direction: column; align-items: flex-start; }
  .source-overview { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .source-overview .metric-tile { padding: 12px; }
  .source-overview .metric-tile__icon { display: none; }
  .source-overview .metric-tile__value { font-size: 20px; margin-top: 6px; }
  .source-overview .metric-tile__hint { display: none; }
  .source-card { min-height: auto; padding: 16px; }
  .mapping-grid { grid-template-columns: 1fr; }
  .editor-top-grid { grid-template-columns: 1fr; gap: 0; }
  .editor-card { padding: 14px; }
  .editor-label-hint { display: block; }
  .source-tabs { padding: 14px; }
}
</style>
