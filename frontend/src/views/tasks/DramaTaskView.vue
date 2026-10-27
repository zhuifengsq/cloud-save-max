<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'

import DramaTaskDrawer from '@/components/tasks/DramaTaskDrawer.vue'
import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'
import {
  createTask,
  createTaskShareLink,
  deleteTask,
  fetchTaskSchedulerSetting,
  fetchTasks,
  stopCompletedDramaTasks,
  setTaskStatus,
  updateTask,
  updateTaskSchedulerSetting,
} from '@/api/tasks'
import { fetchDriveAccounts } from '@/api/extensions'
import { TASK_RUN, TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { DriveAccountItem } from '@/types/extensions'
import type { TaskItem, TaskSchedulerSetting, TaskShareLink } from '@/types/tasks'
import { validateCrontab5, validateTimezone, describeCrontab, getNextExecutions } from '@/utils/cron'
import { detectDriveTypeByUrl } from '@/utils/driveType'
import type { DriveType } from '@/utils/driveType'

const auth = useAuthStore()
const canWrite = computed(() => auth.permissions.includes(TASK_WRITE))
const canRun = computed(() => auth.permissions.includes(TASK_RUN))

const loading = ref(false)
const submitting = ref(false)
const tasks = ref<TaskItem[]>([])
const accounts = ref<DriveAccountItem[]>([])
const scheduler = ref<TaskSchedulerSetting | null>(null)
const schedulerSaving = ref(false)
const cronPreviewVisible = ref(false)
const stopCompletedSaving = ref(false)

const drawerVisible = ref(false)
const currentTask = ref<TaskItem | null>(null)

const runLogDialog = reactive({
  visible: false,
  title: '执行日志',
  content: '',
  status: '',
  stage: '',
  message: '',
  taskId: 0,
})

type RunAllLogItem = {
  taskId: number
  taskname: string
  status: string
  stage: string
  message: string
  content: string
}

const runAllDialog = reactive({
  visible: false,
  title: '执行全部：日志',
  running: false,
  stopRequested: false,
  items: [] as RunAllLogItem[],
  activeNames: [] as Array<string | number>,
})

const deleteDialog = reactive({
  visible: false,
  loading: false,
  task: null as TaskItem | null,
})

const shareDialog = reactive({
  visible: false,
  loadingTaskId: null as number | null,
  taskId: 0,
  fromStored: false,
  result: null as TaskShareLink | null,
})

const runLogPre = ref<HTMLElement | null>(null)
let runLogController: AbortController | null = null
let runAllController: AbortController | null = null
const runAllPreRefs = new Map<number, HTMLElement>()

function setRunAllPreRef(taskId: number) {
  return (el: any) => {
    if (el) runAllPreRefs.set(taskId, el as HTMLElement)
    else runAllPreRefs.delete(taskId)
  }
}

const viewport = reactive({ width: window.innerWidth })
const isMobile = computed(() => viewport.width <= 768)
const showAllActions = computed(() => isMobile.value || viewport.width >= 1560)

const runAllStats = computed(() => {
  const items = runAllDialog.items
  const stats: Record<string, number> = {
    pending: 0,
    running: 0,
    success: 0,
    failed: 0,
    skipped: 0,
    aborted: 0,
    unknown: 0,
  }
  for (const item of items) {
    const key = String(item.status || 'unknown')
    if (Object.prototype.hasOwnProperty.call(stats, key)) stats[key] += 1
    else stats.unknown += 1
  }
  return stats
})

function onResize() {
  viewport.width = window.innerWidth
}

const filters = reactive({
  keyword: '',
  status: 'all',
})

const accountByName = computed(() => new Map(accounts.value.map((item) => [item.name, item])))

function pickDefaultAccountByType(driveType: string) {
  return (
    accounts.value.find((acc) => acc.enabled && acc.drive_type === driveType && acc.is_default) ||
    accounts.value.find((acc) => acc.enabled && acc.drive_type === driveType) ||
    null
  )
}

function formatAccountLabel(task: TaskItem) {
  const name = String(task.account_name || '').trim()
  if (name) {
    const acc = accountByName.value.get(name)
    const driveType = acc?.drive_type || detectDriveTypeByUrl(task.shareurl)
    return driveType ? `${name}（${driveType}）` : name
  }
  const driveType = detectDriveTypeByUrl(task.shareurl)
  const auto = driveType ? pickDefaultAccountByType(driveType) : null
  if (auto && driveType) return `自动：${auto.name}（${driveType}）`
  if (driveType) return `自动（${driveType}）`
  return '自动'
}

function taskDriveType(task: TaskItem): DriveType | null {
  const name = String(task.account_name || '').trim()
  if (name) {
    const driveType = String(accountByName.value.get(name)?.drive_type || '').trim()
    if (driveType) return driveType as DriveType
  }
  return detectDriveTypeByUrl(String(task.shareurl || ''))
}

const filteredTasks = computed(() =>
  tasks.value
    .filter((item) => item.task_type === 'drama')
    .filter((item) => {
      const matchesKeyword =
        !filters.keyword ||
        [item.taskname, item.shareurl, item.savepath, formatAccountLabel(item)]
          .filter(Boolean)
          .some((value) => String(value || '').toLowerCase().includes(filters.keyword.toLowerCase()))
      const matchesStatus =
        filters.status === 'all' ||
        (filters.status === 'enabled' && item.enabled) ||
        (filters.status === 'disabled' && !item.enabled)
      return matchesKeyword && matchesStatus
    }),
)

const showSkeleton = computed(() => loading.value && tasks.value.length === 0)

const hasActiveFilter = computed(() => Boolean(filters.keyword.trim()) || filters.status !== 'all')

function resetFilters() {
  filters.keyword = ''
  filters.status = 'all'
}

async function loadData() {
  loading.value = true
  try {
    const [taskData, accountData, schedulerData] = await Promise.all([
      fetchTasks(),
      fetchDriveAccounts(),
      fetchTaskSchedulerSetting(),
    ])
    tasks.value = taskData
    accounts.value = accountData
    scheduler.value = schedulerData
  } finally {
    loading.value = false
  }
}

async function openCreateDrawer() {
  currentTask.value = null
  drawerVisible.value = true
}


async function openEditDrawer(row: TaskItem) {
  currentTask.value = row
  drawerVisible.value = true
}
async function submitTask(payload: any) {
  submitting.value = true
  try {
    if (currentTask.value) {
      await updateTask(currentTask.value.id, payload)
      await ElMessageBox.alert('任务已更新', '保存成功', { type: 'success', confirmButtonText: '确定' })
    } else {
      await createTask(payload)
      await ElMessageBox.alert('任务已创建', '保存成功', { type: 'success', confirmButtonText: '确定' })
    }
    drawerVisible.value = false
    await loadData()
  } catch (e: any) {
    const msg = formatTaskSaveError(e)
    if (msg) {
      await ElMessageBox.alert(msg, '保存失败', { type: 'error', confirmButtonText: '确定' })
    }
  } finally {
    submitting.value = false
  }
}

function formatTaskSaveError(e: any): string | null {
  const status = Number(e?.response?.status || 0)
  const data = e?.response?.data

  const msg = String(data?.message || '').trim()
  if (msg) return msg

  const detail = data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()

  if (status === 422 && Array.isArray(detail)) {
    const fieldMap: Record<string, string> = {
      taskname: '任务名称',
      shareurl: '分享链接',
      savepath: '保存路径（savepath）',
      task_type: '任务类型',
      tmdb_id: 'TMDB ID',
      tmdb_media_type: 'TMDB 类型',
      account_name: '账号',
    }
    const missing: string[] = []
    const issues: string[] = []
    for (const item of detail) {
      if (!item || typeof item !== 'object') continue
      const loc = Array.isArray((item as any).loc) ? (item as any).loc : []
      const fieldKey = String(loc.at(-1) || '').trim()
      const label = fieldMap[fieldKey] || fieldKey || '参数'
      const rawMsg = String((item as any).msg || '').trim()
      if (!rawMsg) continue
      if (rawMsg.toLowerCase().includes('field required')) {
        if (!missing.includes(label)) missing.push(label)
      } else {
        issues.push(`${label}：${rawMsg}`)
      }
    }
    if (missing.length) return `缺少：${missing.join('、')}`
    if (issues.length) return `保存失败：${issues.slice(0, 3).join('；')}`
    return '保存失败：参数校验异常'
  }

  const fallback = String(e?.message || '').trim()
  return fallback || '保存失败'
}

async function handleToggle(row: TaskItem, enabled: boolean) {
  await setTaskStatus(row.id, enabled)
  ElMessage.success('状态已更新')
  await loadData()
}

function openDeleteDialog(row: TaskItem) {
  deleteDialog.task = row
  deleteDialog.visible = true
}

function closeDeleteDialog() {
  if (deleteDialog.loading) return
  deleteDialog.visible = false
  deleteDialog.task = null
}

async function confirmDelete() {
  const task = deleteDialog.task
  if (!task) return
  deleteDialog.loading = true
  try {
    await deleteTask(task.id)
    const name = String(task.taskname || '').trim()
    ElMessage({
      type: 'success',
      message: name ? `已删除任务：${name}` : '已删除任务',
      showClose: true,
      duration: 2200,
    })
    deleteDialog.visible = false
    deleteDialog.task = null
    await loadData()
  } finally {
    deleteDialog.loading = false
  }
}

function handleRowCommand(command: string, row: TaskItem) {
  if (command === 'share') {
    handleShare(row)
    return
  }
  if (command === 'edit') {
    openEditDrawer(row)
    return
  }
  if (command === 'delete') {
    openDeleteDialog(row)
  }
}

function storedShareLink(row: TaskItem): TaskShareLink | null {
  const record = row.extra?.share_link
  if (!record || !String(record.share_url || '').trim()) return null
  return record
}

function storedShareUrl(row: TaskItem): string {
  return storedShareLink(row)?.share_url || ''
}

function formatShareSavedAt(value?: string | null) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function isShareLinkExpired(result: TaskShareLink | null): boolean {
  const days = Number(result?.expires_in_days || 0)
  const savedAt = String(result?.saved_at || '').trim()
  if (!days || !savedAt) return false
  const saved = new Date(savedAt).getTime()
  if (Number.isNaN(saved)) return false
  return Date.now() > saved + days * 24 * 60 * 60 * 1000
}

function handleShare(row: TaskItem) {
  const stored = storedShareLink(row)
  if (stored) {
    shareDialog.taskId = row.id
    shareDialog.fromStored = true
    shareDialog.result = stored
    shareDialog.visible = true
    return
  }
  generateShare(row)
}

async function generateShare(row: TaskItem) {
  if (shareDialog.loadingTaskId !== null) return
  shareDialog.loadingTaskId = row.id
  shareDialog.taskId = row.id
  try {
    const result = await createTaskShareLink(row.id)
    shareDialog.result = result
    shareDialog.fromStored = false
    shareDialog.visible = true
    // 同步到本地任务列表，不必刷新也能看到已保存的链接
    const target = tasks.value.find((item) => item.id === row.id)
    if (target) {
      target.extra = { ...(target.extra || {}), share_link: result }
    }
  } catch (error: any) {
    ElMessage.error(String(error?.response?.data?.message || error?.message || '生成分享链接失败'))
  } finally {
    shareDialog.loadingTaskId = null
  }
}

function regenerateShare() {
  const row = tasks.value.find((item) => item.id === shareDialog.taskId)
  if (!row) return
  generateShare(row)
}

async function copyStoredShareLink(row: TaskItem) {
  const url = storedShareLink(row)?.share_url
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    ElMessage.success('分享链接已复制')
  } catch {
    ElMessage.warning('复制失败，请点击「分享」查看完整链接')
  }
}

async function copyShareLink() {
  const url = String(shareDialog.result?.share_url || '')
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    ElMessage.success('分享链接已复制')
  } catch {
    ElMessage.warning('复制失败，请选中链接手动复制')
  }
}

async function handleRunStream(opts: { title: string; url: string; body?: any; taskId?: number; reloadAfter?: boolean }) {
  if (runLogController) runLogController.abort()
  runLogController = new AbortController()

  runLogDialog.status = 'running'
  runLogDialog.stage = ''
  runLogDialog.message = ''
  runLogDialog.content = ''
  runLogDialog.title = opts.title
  runLogDialog.visible = true
  runLogDialog.taskId = Number(opts.taskId) || 0

  function appendLine(text: string) {
    runLogDialog.content += `${text}\n`
    nextTick(() => {
      const el = runLogPre.value
      if (!el) return
      el.scrollTop = el.scrollHeight
    })
  }

  function parseSseBlock(block: string) {
    const lines = block.split('\n')
    let eventType = ''
    const dataLines: string[] = []
    for (const line of lines) {
      if (line.startsWith('event:')) eventType = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    return { eventType, dataStr: dataLines.join('\n') }
  }

  try {
    const headers: Record<string, string> = {}
    if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`
    if (opts.body != null) headers['Content-Type'] = 'application/json'
    const response = await fetch(opts.url, {
      method: 'POST',
      headers,
      body: opts.body != null ? JSON.stringify(opts.body) : undefined,
      signal: runLogController.signal,
    })
    if (!response.ok) {
      const text = await response.text().catch(() => '')
      runLogDialog.status = 'failed'
      runLogDialog.message = text || `HTTP ${response.status}`
      ElMessage.error(runLogDialog.message || '任务执行失败')
      return
    }
    const reader = response.body?.getReader()
    if (!reader) {
      runLogDialog.status = 'failed'
      runLogDialog.message = '响应不支持流式读取'
      ElMessage.error(runLogDialog.message)
      return
    }
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        if (!part.trim()) continue
        const parsed = parseSseBlock(part)
        if (!parsed.eventType) continue
        let data: any = null
        try {
          data = parsed.dataStr ? JSON.parse(parsed.dataStr) : null
        } catch {
          data = null
        }
        if (parsed.eventType === 'init') {
          runLogDialog.status = 'running'
          continue
        }
        if (parsed.eventType === 'stage') {
          runLogDialog.stage = String(data?.stage || '')
          continue
        }
        if (parsed.eventType === 'log') {
          appendLine(String(data?.line ?? ''))
          continue
        }
        if (parsed.eventType === 'done') {
          runLogDialog.status = String(data?.status || '')
          runLogDialog.message = String(data?.message || '')
          const exe = data?.execution || null
          if (!runLogDialog.stage) {
            const s = String(exe?.stage || '')
            if (s) runLogDialog.stage = s
          }
          if (!String(runLogDialog.content || '').trim()) {
            const full = String(exe?.run_log || '')
            if (full) runLogDialog.content = full
          }
          const stageTip =
            runLogDialog.stage && !String(runLogDialog.message || '').includes('阶段=')
              ? `（阶段：${runLogDialog.stage}）`
              : ''
          if (runLogDialog.status === 'success') {
            ElMessage.success('任务已执行')
          } else if (runLogDialog.status === 'skipped') {
            ElMessage.info(`${runLogDialog.message || '任务已跳过'}${stageTip}`)
          } else {
            ElMessage.error(`${runLogDialog.message || '任务执行失败'}${stageTip}`)
          }
          if (opts.reloadAfter) await loadData()
          return
        }
      }
    }
    if (runLogDialog.status === 'running') {
      if (opts.reloadAfter) await loadData()
      const id = Number(runLogDialog.taskId) || 0
      if (id > 0) {
        const task = tasks.value.find((t) => t.id === id)
        const list = Array.isArray(task?.executions) ? task!.executions : []
        const pick = [...list].sort((a: any, b: any) => Date.parse(String(b.started_at || '')) - Date.parse(String(a.started_at || '')))[0]
        if (pick) {
          if (!runLogDialog.stage) runLogDialog.stage = String(pick.stage || '')
          if (!String(runLogDialog.content || '').trim()) runLogDialog.content = String(pick.run_log || '')
          runLogDialog.status = String(pick.status || '')
          runLogDialog.message = String(pick.message || '')
        }
      }
    }
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      if (opts.reloadAfter) await loadData()
      const id = Number(runLogDialog.taskId) || 0
      if (id > 0) {
        const task = tasks.value.find((t) => t.id === id)
        const list = Array.isArray(task?.executions) ? task!.executions : []
        const pick = [...list].sort((a: any, b: any) => Date.parse(String(b.started_at || '')) - Date.parse(String(a.started_at || '')))[0]
        if (pick) {
          if (!runLogDialog.stage) runLogDialog.stage = String(pick.stage || '')
          if (!String(runLogDialog.content || '').trim()) runLogDialog.content = String(pick.run_log || '')
          runLogDialog.status = String(pick.status || '')
          runLogDialog.message = String(pick.message || '')
        }
      }
      return
    }
    runLogDialog.status = 'failed'
    runLogDialog.message = e?.message || String(e || '')
    ElMessage.error(runLogDialog.message || '任务执行失败')
  }
}

async function handleRun(row: TaskItem) {
  await handleRunStream({
    title: `执行日志：${row.taskname}`,
    url: `/api/tasks/${row.id}/run/stream`,
    taskId: Number(row.id) || 0,
    reloadAfter: true,
  })
}

function stopRunLogStream() {
  if (!runLogController) return
  runLogController.abort()
  runLogController = null
}

function stopRunAllStream() {
  runAllDialog.stopRequested = true
  if (!runAllController) return
  runAllController.abort()
  runAllController = null
}

async function copyRunAllLog() {
  const blocks = runAllDialog.items.map((item) => {
    const header = `===== ${item.taskname}（${item.status || 'unknown'}）=====`
    const stage = item.stage ? `阶段：${item.stage}\n` : ''
    const msg = item.message ? `结果：${item.message}\n` : ''
    const body = String(item.content || '').trimEnd()
    return `${header}\n${stage}${msg}${body}`.trimEnd()
  })
  const text = blocks.join('\n\n').trim()
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.top = '0'
      textarea.style.left = '0'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      textarea.setSelectionRange(0, 99999)
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    ElMessage.success('日志已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function copyRunLog() {
  const text = String(runLogDialog.content || '')
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.top = '0'
      textarea.style.left = '0'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      textarea.setSelectionRange(0, 99999)
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    ElMessage.success('日志已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function saveScheduler() {
  if (!scheduler.value) return
  const cronCheck = validateCrontab5(scheduler.value.crontab)
  if (!cronCheck.ok) {
    ElMessage.error(cronCheck.message)
    return
  }
  const tzCheck = validateTimezone(scheduler.value.timezone)
  if (!tzCheck.ok) {
    ElMessage.error(tzCheck.message)
    return
  }
  scheduler.value.crontab = cronCheck.normalized || scheduler.value.crontab
  scheduler.value.timezone = tzCheck.normalized || scheduler.value.timezone
  schedulerSaving.value = true
  try {
    scheduler.value = await updateTaskSchedulerSetting({
      enabled: scheduler.value.enabled,
      crontab: scheduler.value.crontab,
      timezone: scheduler.value.timezone,
    })
    ElMessage.success('调度已更新')
  } finally {
    schedulerSaving.value = false
  }
}

async function confirmRunAll() {
  if (runAllDialog.running) return
  try {
    await ElMessageBox.confirm('确认现在手动执行全部“追剧任务”吗？将按当前任务的 runweek 自动跳过不符合条件的任务。', '手动执行', {
      type: 'warning',
      confirmButtonText: '执行',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const target = filteredTasks.value.filter((task) => task.enabled)
  const runnable = target

  runAllDialog.items = runnable.map((task) => ({
    taskId: Number(task.id) || 0,
    taskname: String(task.taskname || '').trim() || `任务 #${task.id}`,
    status: 'pending',
    stage: '',
    message: '',
    content: '',
  }))
  runAllDialog.activeNames = runnable.map((task) => Number(task.id) || 0)
  runAllDialog.title = `执行全部：日志（${runnable.length} 个任务）`
  runAllDialog.visible = true
  runAllDialog.running = true
  runAllDialog.stopRequested = false

  try {
    for (const item of runAllDialog.items) {
      if (runAllDialog.stopRequested) {
        item.status = item.status === 'pending' ? 'aborted' : item.status
        continue
      }

      item.status = 'running'
      item.stage = ''
      item.message = ''
      item.content = ''

      if (runAllController) runAllController.abort()
      runAllController = new AbortController()

      const url = `/api/tasks/${item.taskId}/run/stream`

      function appendLine(text: string) {
        item.content += `${text}\n`
        nextTick(() => {
          const el = runAllPreRefs.get(item.taskId)
          if (!el) return
          el.scrollTop = el.scrollHeight
        })
      }

      function parseSseBlock(block: string) {
        const lines = block.split('\n')
        let eventType = ''
        const dataLines: string[] = []
        for (const line of lines) {
          if (line.startsWith('event:')) eventType = line.slice(6).trim()
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
        }
        return { eventType, dataStr: dataLines.join('\n') }
      }

      try {
        const headers: Record<string, string> = {}
        if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`
        const response = await fetch(url, { method: 'POST', headers, signal: runAllController.signal })
        if (!response.ok) {
          const text = await response.text().catch(() => '')
          item.status = 'failed'
          item.message = text || `HTTP ${response.status}`
          continue
        }
        const reader = response.body?.getReader()
        if (!reader) {
          item.status = 'failed'
          item.message = '响应不支持流式读取'
          continue
        }
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''
          for (const part of parts) {
            if (!part.trim()) continue
            const parsed = parseSseBlock(part)
            if (!parsed.eventType) continue
            let data: any = null
            try {
              data = parsed.dataStr ? JSON.parse(parsed.dataStr) : null
            } catch {
              data = null
            }
            if (parsed.eventType === 'init') {
              item.status = 'running'
              continue
            }
            if (parsed.eventType === 'stage') {
              item.stage = String(data?.stage || '')
              continue
            }
            if (parsed.eventType === 'log') {
              appendLine(String(data?.line ?? ''))
              continue
            }
            if (parsed.eventType === 'done') {
              item.status = String(data?.status || '')
              item.message = String(data?.message || '')
              const exe = data?.execution || null
              if (!item.stage) {
                const s = String(exe?.stage || '')
                if (s) item.stage = s
              }
              if (!String(item.content || '').trim()) {
                const full = String(exe?.run_log || '')
                if (full) item.content = full
              }
              break
            }
          }
          if (item.status !== 'running') break
        }
        if (item.status === 'running') {
          item.status = 'unknown'
          item.message = '执行结束但未收到 done 事件'
        }
      } catch (e: any) {
        if (e?.name === 'AbortError') {
          item.status = runAllDialog.stopRequested ? 'aborted' : 'aborted'
          item.message = runAllDialog.stopRequested ? '已停止读取' : '读取中断'
          continue
        }
        item.status = 'failed'
        item.message = e?.message || String(e || '')
      } finally {
        runAllController = null
      }
    }
  } finally {
    runAllDialog.running = false
    runAllDialog.stopRequested = false
    await loadData()
  }
}
async function confirmStopCompleted() {
  try {
    await ElMessageBox.confirm(
      '确认一键停止（禁用）所有“已完结”的追剧任务吗？\n\n- 仅处理：已启用 + 已关联 TMDB(tv)\n- 依据 TMDB 缓存的 status=Ended/Canceled 判断完结',
      '停止已完结任务',
      {
        type: 'warning',
        confirmButtonText: '停止',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  stopCompletedSaving.value = true
  try {
    const res = await stopCompletedDramaTasks()
    const checked = Number(res?.checked || 0)
    const matched = Number(res?.matched || 0)
    const stopped = Number(res?.stopped || 0)
    await ElMessageBox.alert(`处理完成：checked=${checked}，matched=${matched}，stopped=${stopped}`, '停止结果', {
      type: 'success',
      confirmButtonText: '确定',
    })
    await loadData()
  } catch (e: any) {
    const msg = String(e?.response?.data?.message || e?.response?.data?.detail || e?.message || '停止失败')
    await ElMessageBox.alert(msg, '停止失败', { type: 'error', confirmButtonText: '确定' })
  } finally {
    stopCompletedSaving.value = false
  }
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', onResize, { passive: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="shell-page drama-task-page" v-loading="loading && !showSkeleton">
    <div class="section-header">
      <div class="section-header__title">
        <div class="section-header__eyebrow">Automation</div>
        <h2>追剧任务</h2>
        <div class="section-header__desc">自动转存、失效换链、文件夹对比，一条龙。</div>
      </div>
      <div class="toolbar__right">
        <el-button @click="loadData">刷新</el-button>
        <el-button v-if="canRun" :loading="runAllDialog.running" :disabled="runAllDialog.running" @click="confirmRunAll">执行全部</el-button>
        <el-button v-if="canWrite" :loading="stopCompletedSaving" @click="confirmStopCompleted">停止已完结任务</el-button>
        <el-button v-if="canWrite" type="primary" @click="openCreateDrawer">新增任务</el-button>
      </div>
    </div>

    <section v-if="scheduler" class="glass-panel dashboard-section task-scheduler">
      <div class="task-card-head">
        <div class="task-card-head__title">全局调度</div>
        <div class="task-card-head__hint">cron 定时触发全部启用任务</div>
      </div>
      <div class="toolbar">
        <div class="toolbar__left">
          <el-switch v-model="scheduler.enabled" />
          <span class="task-scheduler__label">启用调度</span>
          <el-input v-model="scheduler.crontab" placeholder="*/15 * * * *" :style="{ width: isMobile ? '100%' : '220px' }">
            <template #append>
              <el-button @click="cronPreviewVisible = true">预览</el-button>
            </template>
          </el-input>
          <span class="task-scheduler__timezone-label">时区</span>
          <el-input v-model="scheduler.timezone" placeholder="Asia/Shanghai" :style="{ width: isMobile ? '100%' : '216px' }" />
        </div>
        <div class="toolbar__right">
          <el-button type="primary" :loading="schedulerSaving" @click="saveScheduler">保存调度</el-button>
        </div>
      </div>
    </section>

    <section class="glass-panel filter-strip">
      <div class="toolbar">
        <div class="toolbar__left">
          <el-input v-model="filters.keyword" clearable placeholder="搜索任务名 / 链接 / 路径" :style="{ width: isMobile ? '100%' : '346px' }" />
          <el-segmented
            v-model="filters.status"
            :options="[
              { label: '全部', value: 'all' },
              { label: '启用', value: 'enabled' },
              { label: '禁用', value: 'disabled' },
            ]"
          />
        </div>
      </div>
    </section>

    <section class="glass-panel dashboard-section task-table-card">
      <div class="task-card-head">
        <div class="task-card-head__title">任务列表</div>
        <div class="task-card-head__hint">共 {{ filteredTasks.length }} 条</div>
      </div>

      <div v-if="showSkeleton" class="task-skeleton" aria-hidden="true">
        <div v-for="i in 6" :key="i" class="task-skeleton__row">
          <span class="task-skeleton__dot"></span>
          <span class="task-skeleton__bar" style="width: 16%"></span>
          <span class="task-skeleton__bar task-skeleton__bar--soft" style="width: 12%"></span>
          <span class="task-skeleton__bar task-skeleton__bar--soft" style="width: 22%"></span>
          <span class="task-skeleton__bar task-skeleton__bar--soft" style="width: 14%"></span>
          <span class="task-skeleton__bar task-skeleton__bar--soft" style="width: 8%"></span>
          <span class="task-skeleton__bar task-skeleton__bar--soft" style="width: 10%"></span>
        </div>
      </div>

      <el-table v-else-if="!isMobile" :data="filteredTasks" style="width: 100%" row-key="id">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div style="padding: 12px 18px">
              <div style="font-weight: 600; margin-bottom: 8px">最近执行</div>
              <div v-if="row.executions?.length" class="task-exec">
                <div v-for="exec in row.executions.slice(0, 3)" :key="exec.id" class="task-exec__item">
                  <div class="task-exec__meta">
                    <span class="task-exec__status">{{ exec.status }}</span>
                    <span class="task-exec__time">{{ exec.started_at }}</span>
                    <span class="task-exec__msg">{{ exec.message }}</span>
                  </div>
                  <pre v-if="exec.tree_summary" class="task-exec__tree">{{ exec.tree_summary }}</pre>
                </div>
              </div>
              <div v-else class="empty-copy">暂无执行记录。</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="任务名" min-width="200">
          <template #default="{ row }">
            <div class="task-name">
              <DriveTypeIcon :type="taskDriveType(row as TaskItem)" />
              <span class="task-name__text" :title="row.taskname">{{ row.taskname }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="账号" width="140">
          <template #default="{ row }">
            <span>{{ formatAccountLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="savepath" label="保存路径" min-width="200" show-overflow-tooltip />
        <el-table-column label="分享链接" width="160">
          <template #default="{ row }">
            <el-tooltip v-if="storedShareUrl(row as TaskItem)" :content="storedShareUrl(row as TaskItem)" placement="top">
              <span class="task-share-link" @click="copyStoredShareLink(row as TaskItem)">{{ storedShareUrl(row as TaskItem) }}</span>
            </el-tooltip>
            <span v-else class="task-share-link task-share-link--empty">未生成</span>
          </template>
        </el-table-column>
        <el-table-column label="完结" width="84">
          <template #default="{ row }">
            <el-tag v-if="row.tmdb_media_type === 'tv' && row.tmdb_is_ended === true" type="success" effect="plain">完结</el-tag>
            <el-tag v-else-if="row.tmdb_media_type === 'tv' && row.tmdb_status" type="warning" effect="plain">连载</el-tag>
            <span v-else class="task-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="enabled" label="状态" width="92">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'primary' : 'danger'" effect="light">{{ row.enabled ? '启用' : '禁用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" :width="showAllActions ? 330 : 200" fixed="right">
          <template #default="{ row }">
            <div v-if="showAllActions" class="task-actions">
              <el-button v-if="canWrite" :type="row.enabled ? 'danger' : 'primary'" text bg  @click="handleToggle(row, !row.enabled)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
              <el-button
                v-if="canRun"
                text
                bg
                type="success"
                @click="handleRun(row)"
              >
                执行
              </el-button>
              <el-button v-if="canWrite" text bg @click="openEditDrawer(row)">编辑</el-button>
              <el-button v-if="canWrite" text bg type="primary" :loading="shareDialog.loadingTaskId === row.id" @click="handleShare(row as TaskItem)">分享</el-button>
              <el-button v-if="canWrite" text bg type="danger"  @click="openDeleteDialog(row)">删除</el-button>
            </div>
            <div v-else class="task-actions">
              <el-button v-if="canWrite" text bg :type="row.enabled ? 'danger' : 'primary'"  @click="handleToggle(row, !row.enabled)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
              <el-button
                v-if="canRun"
                text
                bg
                type="success"
                @click="handleRun(row)"
              >
                执行
              </el-button>
              <el-dropdown v-if="canWrite" trigger="click" @command="(cmd: string) => handleRowCommand(cmd, row)">
                <el-button text bg >更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="edit">编辑</el-dropdown-item>
                    <el-dropdown-item command="share">分享</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-state">
            <div class="empty-state__icon" aria-hidden="true">
              <svg viewBox="0 0 48 48" width="40" height="40" fill="none">
                <rect x="7" y="12" width="34" height="26" rx="6" stroke="currentColor" stroke-width="2" />
                <path d="M7 20h34" stroke="currentColor" stroke-width="2" />
                <path d="M16 27h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                <path d="M16 32h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            </div>
            <div class="empty-state__title">{{ hasActiveFilter ? '没有符合条件的任务' : '还没有追剧任务' }}</div>
            <div class="empty-state__desc">
              {{ hasActiveFilter ? '换个关键词，或把筛选切回「全部」' : '新增任务并填好分享链接和保存路径，之后就会按调度自动追更' }}
            </div>
            <div class="empty-state__actions">
              <el-button v-if="hasActiveFilter" @click="resetFilters">清空筛选</el-button>
              <el-button v-else-if="canWrite" type="primary" @click="openCreateDrawer">新增任务</el-button>
            </div>
          </div>
        </template>
      </el-table>
      <div v-else class="task-card-list">
        <div v-if="!filteredTasks.length" class="task-card-list__empty">
          <div class="empty-state">
            <div class="empty-state__icon" aria-hidden="true">
              <svg viewBox="0 0 48 48" width="40" height="40" fill="none">
                <rect x="7" y="12" width="34" height="26" rx="6" stroke="currentColor" stroke-width="2" />
                <path d="M7 20h34" stroke="currentColor" stroke-width="2" />
                <path d="M16 27h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                <path d="M16 32h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            </div>
            <div class="empty-state__title">{{ hasActiveFilter ? '没有符合条件的任务' : '还没有追剧任务' }}</div>
            <div class="empty-state__desc">
              {{ hasActiveFilter ? '换个关键词，或把筛选切回「全部」' : '新增任务并填好分享链接和保存路径，之后就会按调度自动追更' }}
            </div>
            <div class="empty-state__actions">
              <el-button v-if="hasActiveFilter" @click="resetFilters">清空筛选</el-button>
              <el-button v-else-if="canWrite" type="primary" @click="openCreateDrawer">新增任务</el-button>
            </div>
          </div>
        </div>
        <el-card v-for="row in filteredTasks" :key="row.id" class="task-card" shadow="never">
          <div class="task-card__header">
            <div class="task-card__title">
              <DriveTypeIcon :type="taskDriveType(row)" />
              <span>{{ row.taskname }}</span>
            </div>
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '禁用' }}</el-tag>
          </div>
          <div class="task-card__meta">
            <div class="task-card__meta-row"><span class="task-card__label">账号</span>{{ formatAccountLabel(row) }}</div>
            <div class="task-card__meta-row"><span class="task-card__label">路径</span>{{ row.savepath || '-' }}</div>
            <div v-if="storedShareUrl(row)" class="task-card__meta-row">
              <span class="task-card__label">分享</span>
              <el-tooltip :content="storedShareUrl(row)" placement="top">
                <span class="task-share-link" @click="copyStoredShareLink(row)">{{ storedShareUrl(row) }}</span>
              </el-tooltip>
            </div>
            <div class="task-card__meta-row">
              <span class="task-card__label">完结</span>
              <span v-if="row.tmdb_media_type === 'tv' && row.tmdb_is_ended === true">完结</span>
              <span v-else-if="row.tmdb_media_type === 'tv' && row.tmdb_status">连载</span>
              <span v-else>-</span>
            </div>
          </div>
          <div v-if="row.executions?.length" class="task-card__exec">
            <div class="task-card__exec-title">最近执行</div>
            <div class="task-card__exec-row">
              <span class="task-card__exec-status">{{ row.executions[0].status }}</span>
              <span class="task-card__exec-time">{{ row.executions[0].started_at }}</span>
            </div>
            <div class="task-card__exec-msg">{{ row.executions[0].message }}</div>
          </div>
          <div class="task-card__actions">
            <div class="task-actions">
              <el-button v-if="canWrite" text bg :type="row.enabled ? 'danger' : 'primary'" @click="handleToggle(row, !row.enabled)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
              <el-button
                v-if="canRun"
                text
                bg
                type="success"
                @click="handleRun(row)"
              >
                执行
              </el-button>
              <el-button v-if="canWrite" text bg @click="openEditDrawer(row)">编辑</el-button>
              <el-button v-if="canWrite" text bg type="primary" :loading="shareDialog.loadingTaskId === row.id" @click="handleShare(row)">分享</el-button>
              <el-button v-if="canWrite" text bg type="danger" @click="openDeleteDialog(row)">删除</el-button>
            </div>
          </div>
        </el-card>
      </div>
    </section>

    <DramaTaskDrawer
      v-model="drawerVisible"
      :task="currentTask"
      :accounts="accounts"
      :submitting="submitting"
      @save="submitTask"
    />

    <el-dialog v-model="shareDialog.visible" title="转存文件分享链接" :width="isMobile ? '92vw' : '580px'">
      <template v-if="shareDialog.result">
        <div class="share-result__label">{{ shareDialog.result.title }} · {{ shareDialog.result.account_name }}</div>
        <el-input :model-value="shareDialog.result.share_url" readonly>
          <template #append>
            <el-button @click="copyShareLink">复制链接</el-button>
          </template>
        </el-input>
        <div v-if="shareDialog.result.passcode" class="share-result__hint">提取码：{{ shareDialog.result.passcode }}</div>
        <div v-if="shareDialog.result.expires_in_days" class="share-result__hint">
          有效期：{{ shareDialog.result.expires_in_days }} 天<template v-if="isShareLinkExpired(shareDialog.result)">（已过期，建议重新生成）</template>
        </div>
        <div v-if="shareDialog.fromStored" class="share-result__hint">
          该链接已永久保存在任务里<template v-if="formatShareSavedAt(shareDialog.result.saved_at)">（生成于 {{ formatShareSavedAt(shareDialog.result.saved_at) }}）</template>，重新生成会覆盖这条记录。
        </div>
        <div v-else class="share-result__hint">链接已保存到任务，之后打开「分享」可直接查看、复制。</div>
      </template>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 10px">
          <el-button @click="shareDialog.visible = false">关闭</el-button>
          <el-button
            type="primary"
            :loading="shareDialog.loadingTaskId === shareDialog.taskId"
            @click="regenerateShare"
          >
            重新生成
          </el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="deleteDialog.visible"
      title="删除任务"
      :width="isMobile ? '92vw' : '520px'"
      :close-on-click-modal="false"
      :close-on-press-escape="!deleteDialog.loading"
      :show-close="!deleteDialog.loading"
      @close="closeDeleteDialog"
    >
      <div style="font-size: 14px; line-height: 1.6">
        <div>
          确认删除任务 <span style="font-weight: 600">{{ deleteDialog.task?.taskname || '' }}</span> 吗？
        </div>
        <div style="margin-top: 6px; color: var(--el-text-color-secondary)">
          删除后不可恢复，执行记录也会一并删除。
        </div>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 10px">
          <el-button :disabled="deleteDialog.loading" @click="closeDeleteDialog">取消</el-button>
          <el-button type="danger" :loading="deleteDialog.loading" @click="confirmDelete">删除</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="runLogDialog.visible" :title="runLogDialog.title" :width="isMobile ? '96vw' : '1100px'" :fullscreen="isMobile">
      <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px">
        <el-tag v-if="runLogDialog.status" :type="runLogDialog.status === 'success' ? 'success' : runLogDialog.status === 'running' ? 'warning' : runLogDialog.status === 'skipped' ? 'info' : 'danger'">
          {{ runLogDialog.status }}
        </el-tag>
        <el-tag v-if="runLogDialog.stage" type="warning">阶段：{{ runLogDialog.stage }}</el-tag>
        <el-button style="margin-left: auto" @click="stopRunLogStream">停止读取</el-button>
        <el-button @click="copyRunLog">复制日志</el-button>
      </div>
      <pre ref="runLogPre" style="white-space: pre-wrap; font-size: 12px; line-height: 1.5; background: var(--el-fill-color-blank); border: 1px solid var(--el-border-color-lighter); border-radius: 16px; padding: 12px; max-height: 65vh; overflow: auto">{{ runLogDialog.content }}</pre>
    </el-dialog>

    <el-dialog v-model="runAllDialog.visible" :title="runAllDialog.title" :width="isMobile ? '96vw' : '1100px'" :fullscreen="isMobile">
      <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap">
        <el-tag :type="runAllDialog.running ? 'warning' : 'success'">{{ runAllDialog.running ? 'running' : 'done' }}</el-tag>
        <el-tag v-if="runAllStats.success" type="success">success：{{ runAllStats.success }}</el-tag>
        <el-tag v-if="runAllStats.failed" type="danger">failed：{{ runAllStats.failed }}</el-tag>
        <el-tag v-if="runAllStats.skipped" type="info">skipped：{{ runAllStats.skipped }}</el-tag>
        <el-tag v-if="runAllStats.aborted" type="warning">aborted：{{ runAllStats.aborted }}</el-tag>
        <el-tag v-if="runAllStats.pending" type="info">pending：{{ runAllStats.pending }}</el-tag>
        <el-tag v-if="runAllStats.unknown" type="danger">unknown：{{ runAllStats.unknown }}</el-tag>
        <el-button style="margin-left: auto" :disabled="!runAllDialog.running" @click="stopRunAllStream">停止全部</el-button>
        <el-button :disabled="!runAllDialog.items.length" @click="copyRunAllLog">复制全部日志</el-button>
      </div>

      <el-collapse v-model="runAllDialog.activeNames">
        <el-collapse-item v-for="item in runAllDialog.items" :key="item.taskId" :name="item.taskId">
          <template #title>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <el-tag
                v-if="item.status"
                :type="
                  item.status === 'success'
                    ? 'success'
                    : item.status === 'running'
                      ? 'warning'
                      : item.status === 'skipped'
                        ? 'info'
                        : item.status === 'pending'
                          ? 'info'
                          : item.status === 'aborted'
                            ? 'warning'
                            : 'danger'
                "
              >
                {{ item.status }}
              </el-tag>
              <span style="font-weight: 600">{{ item.taskname }}</span>
              <span v-if="item.stage" style="font-size: 12px; color: var(--el-text-color-secondary)">阶段：{{ item.stage }}</span>
              <span v-if="item.message" style="font-size: 12px; color: var(--el-text-color-secondary)">{{ item.message }}</span>
            </div>
          </template>
          <pre :ref="setRunAllPreRef(item.taskId)" class="run-all-pre">{{ item.content }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-dialog>

    <!-- Cron 预览弹窗 -->
    <el-dialog v-model="cronPreviewVisible" title="执行计划预览" width="480px" :close-on-click-modal="true">
      <div v-if="scheduler">
        <div style="margin-bottom: 16px;">
          <div style="font-weight: 600; margin-bottom: 8px;">执行规则</div>
          <div style="color: var(--el-text-color-regular);">{{ describeCrontab(scheduler.crontab) || '无法解析' }}</div>
        </div>
        <div style="margin-bottom: 16px;">
          <div style="font-weight: 600; margin-bottom: 8px;">cron 表达式</div>
          <div style="font-family: monospace; color: var(--el-text-color-regular);">{{ scheduler.crontab }}</div>
        </div>
        <div>
          <div style="font-weight: 600; margin-bottom: 8px;">接下来执行时间</div>
          <div v-if="getNextExecutions(scheduler.crontab, 5).length > 0">
            <div v-for="(time, idx) in getNextExecutions(scheduler.crontab, 5)" :key="idx" style="color: var(--el-text-color-regular); padding: 4px 0;">
              {{ time.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' }) }}
            </div>
            <div style="color: var(--el-text-color-placeholder); padding: 4px 0;">...</div>
          </div>
          <div v-else style="color: var(--el-text-color-placeholder);">无法计算</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.task-exec {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-exec__item {
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
}

.task-exec__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.task-exec__status {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 11.5px;
  font-weight: 650;
}

.task-exec__time {
  font-variant-numeric: tabular-nums;
}

.task-exec__msg {
  color: var(--el-text-color-regular);
}

.task-exec__tree {
  max-height: 220px;
  margin: 10px 0 0;
  padding: 10px 12px;
  overflow: auto;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--el-text-color-regular);
}

.task-card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-card {
  border-radius: 18px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-blank);
}

.task-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.task-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.task-card__meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.task-card__meta-row {
  display: flex;
  gap: 10px;
  align-items: baseline;
}

.run-all-pre {
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.5;
  background: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 16px;
  padding: 12px;
  max-height: 45vh;
  overflow: auto;
}

.task-card__label {
  width: 36px;
  color: var(--el-text-color-primary);
  font-weight: 600;
}

.task-card__exec {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color);
}

.task-card__exec-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin-bottom: 6px;
}

.task-card__exec-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.task-card__exec-status {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.task-card__exec-msg {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.task-card__actions {
  margin-top: 12px;
}

.share-result__label {
  margin-bottom: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.share-result__hint {
  margin-top: 10px;
  color: var(--el-text-color-secondary);
}

.task-share-link {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  color: var(--el-color-primary);
  cursor: pointer;
}

.task-share-link--empty {
  color: var(--el-text-color-placeholder);
  cursor: default;
}

.task-empty {
  color: var(--el-text-color-placeholder);
}

.task-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.task-name__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.task-actions .el-button {
  height: 28px;
  border-radius: 8px;
}

.task-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
}

.task-actions .el-button {
  padding: 7px 9px;
  font-size: 12px;
  white-space: nowrap;
}

.drama-task-page > .section-header .toolbar__right {
  gap: 8px;
}

.drama-task-page > .section-header .el-button {
  margin: 0;
}

.task-scheduler .toolbar {
  align-items: flex-end;
  padding: 16px 20px 18px;
}

.task-scheduler {
  padding: 0;
  overflow: hidden;
}

.task-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.task-card-head__title {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--el-text-color-primary);
}

.task-card-head__hint {
  font-size: 12.5px;
  color: var(--el-text-color-secondary);
}

.task-table-card {
  padding: 0;
  overflow: hidden;
}

.task-table-card :deep(.el-table__inner-wrapper::before) {
  display: none;
}

.task-table-card :deep(.el-table__empty-block) {
  min-height: 232px;
}

/* 骨架屏 */
.task-skeleton {
  padding: 8px 0;
}

.task-skeleton__row {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 15px 20px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.task-skeleton__row:last-child {
  border-bottom: 0;
}

.task-skeleton__dot {
  flex: 0 0 20px;
  width: 20px;
  height: 20px;
  border-radius: 6px;
}

.task-skeleton__bar {
  height: 11px;
  border-radius: 6px;
}

.task-skeleton__bar--soft {
  opacity: 0.55;
}

.task-skeleton__dot,
.task-skeleton__bar {
  background: linear-gradient(
    90deg,
    var(--el-fill-color-light) 25%,
    var(--el-fill-color) 37%,
    var(--el-fill-color-light) 63%
  );
  background-size: 400% 100%;
  animation: task-shimmer 1.4s ease infinite;
}

@keyframes task-shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 40px 20px 44px;
  text-align: center;
}

.empty-state__icon {
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  margin-bottom: 8px;
  border-radius: 18px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.empty-state__title {
  font-size: 15px;
  font-weight: 650;
  color: var(--el-text-color-primary);
}

.empty-state__desc {
  max-width: 400px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.empty-state__actions {
  margin-top: 10px;
}

.task-card-list__empty {
  padding: 4px;
}

@media (max-width: 768px) {
  .task-table-card {
    padding: 14px;
  }
}

.task-scheduler .el-switch {
  margin-right: 8px;
}

.task-scheduler__label {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-right: 6px;
}

.task-card {
  border-radius: 12px;
}

.task-actions__first {
  margin-right: 14px;
}

.task-actions__item {
  margin-right: 6px;
}

.task-actions__last {
  margin-right: 0;
}

.task-scheduler__timezone-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  white-space: nowrap;
}


@media (max-width: 767px) {
  .drama-task-page > .section-header { min-height: 0; }
  .drama-task-page .section-header__title h2 { font-size: 21px; }
  .drama-task-page .task-scheduler .toolbar { padding: 16px; }
  .task-scheduler__timezone-label { display: none; }
}

@media (max-width: 768px) {
  .section-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }

  .toolbar__right {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
    gap: 10px;
  }

  .toolbar__left {
    width: 100%;
    flex-wrap: wrap;
    gap: 10px;
  }
}
</style>
