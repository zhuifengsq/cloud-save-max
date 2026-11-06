<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'

import DramaLeadInDirPicker from '@/components/extensions/DramaLeadInDirPicker.vue'
import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'

import {
  fetchDramaLeadInSettings,
  readLeadInErrorMessage,
  resolveDramaLeadInOwnFile,
  scanDramaLeadInMaterial,
  updateDramaLeadInSettings,
} from '@/api/dramaLeadIn'
import { fetchDriveAccounts } from '@/api/extensions'
import { deleteMagicRegexRule, fetchMagicRegexRules, upsertMagicRegexRule } from '@/api/magicRegex'
import { fetchTMDBConfig, patchTMDBConfig } from '@/api/tmdb'
import { fetchTaskTemplates, createTaskTemplate, updateTaskTemplate, deleteTaskTemplate } from '@/api/taskTemplates'
import type { TaskTemplate } from '@/api/taskTemplates'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type {
  DramaLeadInMaterialInput,
  DramaLeadInMaterialMode,
  DramaLeadInOwnFile,
  DramaLeadInScanFile,
  DramaLeadInSettings,
} from '@/types/dramaLeadIn'
import type { DriveAccountItem } from '@/types/extensions'
import type { MagicRegexRuleSetting } from '@/types/magicRegex'
import type { TMDBConfig } from '@/types/tmdb'
import { formatBytes } from '@/utils/capacity'
import { formatDateTimeText } from '@/utils/datetime'

const auth = useAuthStore()
const canWrite = computed(() => auth.permissions.includes(TASK_WRITE))

const loading = ref(false)
const rules = ref<MagicRegexRuleSetting[]>([])
const variables = ref<Record<string, string>>({})
const activeTab = ref('magic_regex')

const templates = reactive({
  loading: false,
  list: [] as TaskTemplate[],
  dialogVisible: false,
  editingId: null as number | null,
  form: {
    name: '',
    config: {} as Record<string, any>,
  },
  // 内置规则
  magicRegex: {
    loading: false,
    selectedKey: '',
    rules: [] as Array<{ key: string; label: string; pattern: string; replace: string }>,
  },
  // 关键词过滤预设
  filterWords: {
    options: [] as Array<{ name: string; keywords: string }>,
  },
})

const TEMPLATE_FIELDS = [
  { key: 'search_filter', label: '搜索筛选词', type: 'keyword', hint: '搜索结果标题必须包含这些词才会显示，用 | 分隔' },
  { key: 'search_exclude', label: '搜索过滤词', type: 'keyword', hint: '搜索结果标题包含这些词将被过滤，用 | 分隔' },
  { key: 'folder_filter', label: '文件夹筛选', type: 'keyword', hint: '只转存文件夹名称包含这些词的目录，用 | 分隔' },
  { key: 'folder_exclude', label: '文件夹过滤', type: 'keyword', hint: '跳过文件夹名称包含这些词的目录，用 | 分隔' },
  { key: 'folder_priority', label: '文件夹优先级', type: 'keyword', hint: '匹配的文件夹将优先转存，用 | 分隔' },
  { key: 'folder_mode', label: '文件夹模式', type: 'text', hint: 'normal=普通平铺；folder_diff=文件夹对比（白名单增量 + 其它文件夹整包一次）' },
  { key: 'whitelist_folders', label: '白名单文件夹', type: 'keyword', hint: '文件夹对比模式下按相对路径+文件名增量的文件夹，用 | 分隔' },
  { key: 'filter_words', label: '关键词过滤', type: 'filter_words', hint: '转存时过滤掉文件名包含这些关键词的文件' },
  { key: 'file_filter', label: '关键词筛选', type: 'keyword', hint: '只转存文件名包含这些关键词的文件，用 | 分隔' },
  { key: 'min_size', label: '最小文件大小', type: 'text', hint: '小于此大小的文件将被过滤，如：100MB' },
]

const tmdb = reactive({
  loading: false,
  saving: false,
  hasApiKey: false,
  language: 'zh-CN',
  posterLanguage: 'zh-CN',
  apiKeyInput: '',
})

const sharerFilter = reactive({
  loading: false,
  saving: false,
  preferred: [] as string[],
  blocked: [] as string[],
  validate_batch_size: 5,
  preview_cache_ttl_seconds: 300,
  search_cache_ttl_seconds: 3,
})

const sharerDialog = reactive({
  visible: false,
  type: 'preferred' as 'preferred' | 'blocked',
  inputValue: '',
})

const filterRules = reactive({
  loading: false,
  rules: [] as Array<{ name: string; keywords: string }>,
  dialogVisible: false,
  editIndex: -1,
  form: { name: '', keywords: '' },
})
type LeadInDriveForm = {
  drive_type: string
  drive_name: string
  supported: boolean
  reason: string
  configured: boolean
  shareurlMasked: string
  passcodeSet: boolean
  configVersion: number
  updatedAt: string
  shareurl: string
  passcode: string
  selectedFid: string
  fileName: string
  files: DramaLeadInScanFile[]
  scannedShareurl: string
  scanned: boolean
  scanning: boolean
  dirty: boolean
  shareurlTouched: boolean
  passcodeTouched: boolean
  clearing: boolean
  mode: DramaLeadInMaterialMode
  ownFileSupported: boolean
  ownFileBrowseSupported: boolean
  materialAccount: string
  materialFilePath: string
  materialFileName: string
  materialFileSize: number | null
  savedAccount: string
  savedFilePath: string
  savedFileName: string
  resolvedOwnFile: DramaLeadInOwnFile | null
  resolving: boolean
}

const dramaLeadIn = reactive({
  loading: false,
  saving: false,
  enabled: false,
  drives: [] as LeadInDriveForm[],
  accounts: [] as DriveAccountItem[],
  // 折叠面板当前展开的网盘（accordion，一次只展开一个）。
  activeDrive: '',
})

// 目录浏览弹窗状态：只服务本账号文件模式的路径选择，不缓存跨账号目录内容。
const leadInDirPicker = reactive({
  visible: false,
  driveType: '',
  driveName: '',
  accountName: '',
  startPath: '',
  drive: null as LeadInDriveForm | null,
})

function applyDramaLeadInSettings(data: DramaLeadInSettings) {
  dramaLeadIn.enabled = Boolean(data?.enabled)
  dramaLeadIn.drives = (data?.drives || []).map((drive) => ({
    drive_type: String(drive.drive_type || ''),
    drive_name: String(drive.drive_name || drive.drive_type || ''),
    supported: Boolean(drive.supported),
    reason: String(drive.reason || ''),
    configured: Boolean(drive.configured),
    shareurlMasked: String(drive.shareurl || ''),
    passcodeSet: Boolean(drive.passcode_set),
    configVersion: Number(drive.config_version) || 0,
    updatedAt: String(drive.updated_at || ''),
    shareurl: '',
    passcode: '',
    selectedFid: String(drive.file_fid || ''),
    fileName: String(drive.file_name || ''),
    files: [],
    scannedShareurl: '',
    scanned: false,
    scanning: false,
    dirty: false,
    shareurlTouched: false,
    passcodeTouched: false,
    clearing: false,
    mode: drive.mode === 'own_file' ? 'own_file' : 'share',
    ownFileSupported: Boolean(drive.own_file_supported),
    ownFileBrowseSupported: Boolean(drive.own_file_browse_supported),
    materialAccount: String(drive.account_name || ''),
    materialFilePath: String(drive.file_path || ''),
    materialFileName: String(drive.file_name || ''),
    materialFileSize: typeof drive.file_size === 'number' ? drive.file_size : null,
    savedAccount: String(drive.account_name || ''),
    savedFilePath: String(drive.file_path || ''),
    savedFileName: String(drive.file_name || ''),
    resolvedOwnFile: null,
    resolving: false,
  }))
  syncLeadInActiveDrive()
}

// 折叠面板默认展开：已配置的网盘优先，其次第一个支持的网盘。
function syncLeadInActiveDrive() {
  const drives = dramaLeadIn.drives
  if (!drives.length) {
    dramaLeadIn.activeDrive = ''
    return
  }
  if (drives.some((drive) => drive.drive_type === dramaLeadIn.activeDrive)) return
  const preferred =
    drives.find((drive) => drive.supported && drive.configured) ||
    drives.find((drive) => drive.supported) ||
    drives[0]
  dramaLeadIn.activeDrive = preferred.drive_type
}

// 折叠标题右侧的摘要：收起时也能看出配置了哪个素材。
function leadInCollapseSummary(drive: LeadInDriveForm) {
  if (!drive.supported) return '暂不支持引流'
  if (!drive.configured) return '未配置'
  if (drive.mode === 'own_file') {
    const name = drive.materialFileName || drive.fileName
    return name ? `本账号文件：${name}` : '本账号文件'
  }
  return drive.fileName ? `分享文件：${drive.fileName}` : '分享链接'
}

async function loadDramaLeadIn() {
  dramaLeadIn.loading = true
  try {
    // 「本账号文件」需要账号列表；账号加载失败不应阻塞配置展示。
    const [settings] = await Promise.all([
      fetchDramaLeadInSettings(),
      fetchDriveAccounts()
        .then((accounts) => {
          dramaLeadIn.accounts = accounts || []
        })
        .catch(() => {
          dramaLeadIn.accounts = []
        }),
    ])
    applyDramaLeadInSettings(settings)
  } catch (error) {
    ElMessage.error(readLeadInErrorMessage(error, '加载引流配置失败'))
  } finally {
    dramaLeadIn.loading = false
  }
}

function pickSingleLeadInFile(files: DramaLeadInScanFile[]) {
  if (files.length !== 1 || files[0].dir) return ''
  return String(files[0].fid || '')
}

function leadInSelectedName(drive: LeadInDriveForm) {
  const selected = drive.files.find((file) => String(file.fid) === drive.selectedFid)
  return selected?.name || drive.fileName || ''
}

function onLeadInShareurlInput(drive: LeadInDriveForm) {
  drive.dirty = true
  drive.shareurlTouched = true
  drive.clearing = false
  // 链接变化后旧的文件列表与已选素材都不再可信，需要重新读取并选择。
  if (drive.scanned && drive.shareurl.trim() !== drive.scannedShareurl) {
    drive.scanned = false
    drive.scannedShareurl = ''
    drive.files = []
  }
  drive.selectedFid = ''
  drive.fileName = ''
}

function onLeadInPasscodeInput(drive: LeadInDriveForm) {
  drive.dirty = true
  drive.passcodeTouched = true
}

function onLeadInModeChange(drive: LeadInDriveForm) {
  drive.dirty = true
  drive.clearing = false
  // 切换素材来源后，之前读取到的本账号文件信息不再对应当前来源。
  drive.resolvedOwnFile = null
}

function onLeadInOwnFileInput(drive: LeadInDriveForm) {
  drive.dirty = true
  drive.clearing = false
  // 账号或路径变化后，已读取的文件信息可能不再对应，需要重新读取确认。
  drive.resolvedOwnFile = null
}

function leadInAccountsFor(drive: LeadInDriveForm) {
  // 与后端一致：只接受已启用且网盘类型匹配的账号。
  return dramaLeadIn.accounts.filter(
    (account) => account.enabled && account.drive_type === drive.drive_type,
  )
}

function leadInAccountLabel(account: DriveAccountItem) {
  const nickname = String(account.profile?.nickname || '').trim()
  return nickname && nickname !== account.name ? `${account.name}（${nickname}）` : account.name
}

function leadInOwnFileSummary(drive: LeadInDriveForm) {
  if (drive.resolvedOwnFile) {
    const size = formatBytes(drive.resolvedOwnFile.size)
    return `已读取：${drive.resolvedOwnFile.name}${size === '--' ? '' : `（${size}）`}`
  }
  if (drive.materialFileName) {
    const size = formatBytes(drive.materialFileSize)
    return `当前素材：${drive.materialFileName}${size === '--' ? '' : `（${size}）`}`
  }
  return '未读取'
}

async function resolveLeadInOwnFile(drive: LeadInDriveForm) {
  if (!canWrite.value) return
  const accountName = drive.materialAccount.trim()
  const filePath = drive.materialFilePath.trim()
  if (!accountName) {
    ElMessage.warning('请先选择素材账号')
    return
  }
  if (!filePath) {
    ElMessage.warning('请先填写账号内文件的完整路径')
    return
  }
  if (!filePath.startsWith('/')) {
    ElMessage.warning('文件路径需以 / 开头，例如 /素材/引流说明.txt')
    return
  }
  drive.resolving = true
  try {
    const result = await resolveDramaLeadInOwnFile({
      drive_type: drive.drive_type,
      account_name: accountName,
      file_path: filePath,
    })
    const file = result?.file
    if (!file || file.dir) {
      ElMessage.error('只能选择文件，不能选择文件夹')
      return
    }
    drive.resolvedOwnFile = {
      fid: String(file.fid || ''),
      name: String(file.name || ''),
      path: String(file.path || filePath),
      size: typeof file.size === 'number' ? file.size : null,
      md5: String(file.md5 || ''),
      dir: Boolean(file.dir),
    }
    drive.materialFileName = drive.resolvedOwnFile.name
    drive.materialFileSize = drive.resolvedOwnFile.size ?? null
    ElMessage.success(`已读取素材文件：${drive.resolvedOwnFile.name}`)
  } catch (error) {
    drive.resolvedOwnFile = null
    ElMessage.error(readLeadInErrorMessage(error, '读取本账号文件失败'))
  } finally {
    drive.resolving = false
  }
}

function leadInBrowseStartPath(drive: LeadInDriveForm) {
  const path = drive.materialFilePath.trim()
  if (!path || path === '/') return '/'
  const segments = path.split('/').filter(Boolean)
  return segments.length > 1 ? `/${segments.slice(0, -1).join('/')}` : '/'
}

function openLeadInDirPicker(drive: LeadInDriveForm) {
  if (!canWrite.value) return
  const accountName = drive.materialAccount.trim()
  if (!accountName) {
    ElMessage.warning('请先选择素材账号')
    return
  }
  leadInDirPicker.driveType = drive.drive_type
  leadInDirPicker.driveName = drive.drive_name
  leadInDirPicker.accountName = accountName
  // 已填写的完整路径是文件路径，浏览时从所在目录开始，避免直接列一个文件而报错。
  leadInDirPicker.startPath = leadInBrowseStartPath(drive)
  leadInDirPicker.drive = drive
  leadInDirPicker.visible = true
}

async function onLeadInDirPicked(path: string) {
  const drive = leadInDirPicker.drive
  leadInDirPicker.visible = false
  if (!drive) return
  // 选中目录文件后仍走现有读取接口确认，成功才回填文件名/大小；失败保留手填路径。
  drive.materialFilePath = path
  drive.materialFileName = ''
  drive.materialFileSize = null
  onLeadInOwnFileInput(drive)
  await resolveLeadInOwnFile(drive)
}

async function clearLeadInDrive(drive: LeadInDriveForm) {
  try {
    await ElMessageBox.confirm(
      `确定清空「${drive.drive_name}」的引流配置？保存后生效，该网盘将不再提供引流文件。`,
      '清空引流配置',
      { type: 'warning' },
    )
  } catch {
    return
  }
  drive.clearing = true
  drive.dirty = true
  drive.shareurl = ''
  drive.passcode = ''
  drive.selectedFid = ''
  drive.fileName = ''
  drive.files = []
  drive.scanned = false
  drive.scannedShareurl = ''
  drive.materialAccount = ''
  drive.materialFilePath = ''
  drive.materialFileName = ''
  drive.materialFileSize = null
  drive.resolvedOwnFile = null
}

async function scanLeadInDrive(drive: LeadInDriveForm) {
  if (!canWrite.value) return
  const shareurl = drive.shareurl.trim()
  if (!shareurl) {
    ElMessage.warning('请先填写完整的分享链接再读取文件')
    return
  }
  drive.scanning = true
  try {
    const result = await scanDramaLeadInMaterial({
      drive_type: drive.drive_type,
      shareurl,
      passcode: drive.passcode.trim(),
    })
    drive.files = (result?.files || [])
      .map((file) => ({ fid: String(file.fid || ''), name: String(file.name || ''), dir: Boolean(file.dir) }))
      .filter((file) => Boolean(file.fid))
    drive.scanned = true
    drive.scannedShareurl = shareurl
    drive.dirty = true
    const preferredFid = String(result?.selected_fid || '')
    const preferred = drive.files.find((file) => String(file.fid) === preferredFid && !file.dir)
    drive.selectedFid = preferred ? String(preferred.fid) : pickSingleLeadInFile(drive.files)
    if (!drive.files.length) {
      ElMessage.warning('分享内没有可用文件')
    } else if (!drive.selectedFid) {
      ElMessage.info('分享内有多个文件，请选择素材文件')
    } else {
      ElMessage.success('已读取分享文件')
    }
  } catch (error) {
    ElMessage.error(readLeadInErrorMessage(error, '读取分享文件失败'))
  } finally {
    drive.scanning = false
  }
}

async function saveDramaLeadIn() {
  if (!canWrite.value) return
  const materials: DramaLeadInMaterialInput[] = []
  for (const drive of dramaLeadIn.drives) {
    if (!drive.supported || (!drive.dirty && !drive.clearing)) continue
    const mode: DramaLeadInMaterialMode = drive.mode === 'own_file' ? 'own_file' : 'share'
    if (drive.clearing) {
      // 清空配置：两种模式统一提交空字段，由后端删除该网盘配置。
      materials.push({
        drive_type: drive.drive_type,
        mode,
        shareurl: '',
        passcode: '',
        file_fid: '',
        file_name: '',
        account_name: '',
        file_path: '',
      })
      continue
    }
    if (mode === 'own_file') {
      if (!drive.ownFileSupported) {
        ElMessage.warning(`「${drive.drive_name}」暂不支持本账号文件`)
        return
      }
      const accountName = drive.materialAccount.trim()
      const filePath = drive.materialFilePath.trim()
      if (!accountName) {
        ElMessage.warning(`「${drive.drive_name}」请先选择素材账号`)
        return
      }
      if (!filePath) {
        ElMessage.warning(`「${drive.drive_name}」请填写账号内文件的完整路径`)
        return
      }
      // 路径/账号未变时可沿用已保存的文件名，否则必须重新「读取文件」确认。
      const resolved = drive.resolvedOwnFile
      const matched = resolved && resolved.path.trim() === filePath ? resolved : null
      const fileName = matched
        ? matched.name
        : filePath === drive.savedFilePath && accountName === drive.savedAccount
          ? drive.savedFileName
          : ''
      if (!fileName) {
        ElMessage.warning(`「${drive.drive_name}」请先点击「读取文件」确认素材文件`)
        return
      }
      materials.push({
        drive_type: drive.drive_type,
        mode: 'own_file',
        account_name: accountName,
        file_path: filePath,
        file_name: fileName,
        shareurl: '',
        passcode: '',
        file_fid: matched?.fid || '',
      })
      continue
    }
    // 分享链接模式：保持原有校验与清空语义，避免回归。
    const shareurl = drive.shareurl.trim()
    if (!shareurl) {
      if (drive.shareurlTouched) {
        // 链接被手动清空后保存，视为清空该网盘的引流配置。
        materials.push({
          drive_type: drive.drive_type,
          mode: 'share',
          shareurl: '',
          passcode: '',
          file_fid: '',
          file_name: '',
        })
        continue
      }
      // 已配置网盘的完整链接不回显，仅修改提取码无法单独提交，避免误清空配置。
      ElMessage.warning(`「${drive.drive_name}」请重新填写完整分享链接，或点击「清空配置」`)
      return
    }
    if (drive.scanned && !drive.selectedFid) {
      ElMessage.warning(`「${drive.drive_name}」分享内包含多个文件，请先选择素材文件`)
      return
    }
    const selected = drive.files.find((file) => String(file.fid) === drive.selectedFid)
    materials.push({
      drive_type: drive.drive_type,
      mode: 'share',
      shareurl,
      passcode: drive.passcode.trim(),
      file_fid: drive.selectedFid,
      file_name: selected?.name || drive.fileName,
    })
  }
  dramaLeadIn.saving = true
  try {
    applyDramaLeadInSettings(await updateDramaLeadInSettings({ enabled: dramaLeadIn.enabled, materials }))
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(readLeadInErrorMessage(error, '保存引流配置失败'))
  } finally {
    dramaLeadIn.saving = false
  }
}

const builtinKeySet = computed(() => new Set(rules.value.filter((r) => r.built_in).map((r) => r.key)))
const currentIsBuiltinKey = computed(() => builtinKeySet.value.has(normalizeKey(dialog.form.key)))

const dialog = reactive({
  visible: false,
  submitting: false,
  isEdit: false,
  keyLocked: false,
  form: {
    key: '',
    label: '' as string | null,
    enabled: true,
    pattern: '',
    replace: '',
  },
})

function normalizeKey(key: string) {
  return String(key || '').trim()
}

function isValidKey(key: string) {
  const value = normalizeKey(key)
  return value.startsWith('$') && !value.includes(' ') && value.length <= 64
}

async function refresh() {
  loading.value = true
  try {
    const data = await fetchMagicRegexRules()
    rules.value = data.rules || []
    variables.value = data.variables || {}
  } finally {
    loading.value = false
  }
}

function applyTMDBConfig(data: TMDBConfig) {
  tmdb.hasApiKey = Boolean(data.has_api_key)
  tmdb.language = String(data.language || 'zh-CN')
  tmdb.posterLanguage = String(data.poster_language || 'zh-CN')
}

async function refreshTMDB() {
  tmdb.loading = true
  try {
    const data = await fetchTMDBConfig()
    applyTMDBConfig(data)
  } finally {
    tmdb.loading = false
  }
}

function removeSharer(name: string) {
  if (sharerDialog.type === 'preferred') {
    sharerFilter.preferred = sharerFilter.preferred.filter((x) => x !== name)
  } else {
    sharerFilter.blocked = sharerFilter.blocked.filter((x) => x !== name)
  }
}

function addSharerFromInput() {
  const raw = (sharerDialog.inputValue || '').trim()
  if (!raw) return
  const names = raw.split('|').map((s: string) => s.trim()).filter(Boolean)
  if (sharerDialog.type === 'preferred') {
    for (const name of names) {
      if (!sharerFilter.preferred.includes(name)) sharerFilter.preferred.push(name)
    }
  } else {
    for (const name of names) {
      if (!sharerFilter.blocked.includes(name)) sharerFilter.blocked.push(name)
    }
  }
  sharerDialog.inputValue = ''
}


async function loadSharerFilter() {
  sharerFilter.loading = true
  try {
    const { fetchSharerFilterSettings } = await import('@/api/systemSettings')
    const data = await fetchSharerFilterSettings()
    sharerFilter.preferred = (data.preferred_sharers || '').split('|').map((s: string) => s.trim()).filter(Boolean)
    sharerFilter.blocked = (data.blocked_sharers || '').split('|').map((s: string) => s.trim()).filter(Boolean)
    sharerFilter.validate_batch_size = data.validate_batch_size || 5
    sharerFilter.preview_cache_ttl_seconds = data.preview_cache_ttl_seconds || 300
    sharerFilter.search_cache_ttl_seconds = data.search_cache_ttl_seconds ?? 3
  } finally {
    sharerFilter.loading = false
  }
}

async function saveSharerFilter() {
  if (!canWrite.value) return
  sharerFilter.saving = true
  try {
    const { updateSharerFilterSettings } = await import('@/api/systemSettings')
    const payload = {
      preferred_sharers: sharerFilter.preferred.join('|') || null,
      blocked_sharers: sharerFilter.blocked.join('|') || null,
      validate_batch_size: Number(sharerFilter.validate_batch_size) || 5,
      preview_cache_ttl_seconds: Number(sharerFilter.preview_cache_ttl_seconds) || 300,
      search_cache_ttl_seconds: Number(sharerFilter.search_cache_ttl_seconds) || 3,
    }
    const data = await updateSharerFilterSettings(payload)
    sharerFilter.preferred = (data.preferred_sharers || '').split('|').map((s: string) => s.trim()).filter(Boolean)
    sharerFilter.blocked = (data.blocked_sharers || '').split('|').map((s: string) => s.trim()).filter(Boolean)
    sharerFilter.validate_batch_size = data.validate_batch_size || 5
    sharerFilter.preview_cache_ttl_seconds = data.preview_cache_ttl_seconds || 300
    sharerFilter.search_cache_ttl_seconds = data.search_cache_ttl_seconds ?? 3
    ElMessage.success('已保存')
  } finally {
    sharerFilter.saving = false
  }
}

async function loadFilterRules() {
  filterRules.loading = true
  try {
    const { fetchFilterRules } = await import('@/api/systemSettings')
    filterRules.rules = await fetchFilterRules()
  } finally {
    filterRules.loading = false
  }
}

function openFilterRuleDialog(index: number) {
  if (index >= 0) {
    filterRules.editIndex = index
    filterRules.form = { ...filterRules.rules[index] }
  } else {
    filterRules.editIndex = -1
    filterRules.form = { name: '', keywords: '' }
  }
  filterRules.dialogVisible = true
}

async function saveFilterRule() {
  const name = filterRules.form.name.trim()
  if (!name) return ElMessage.warning('请输入名称')
  const rules = [...filterRules.rules]
  if (filterRules.editIndex >= 0) {
    rules[filterRules.editIndex] = { ...filterRules.form }
  } else {
    if (rules.some((r) => r.name === name)) return ElMessage.warning('名称已存在')
    rules.push({ ...filterRules.form })
  }
  try {
    const { saveFilterRules } = await import('@/api/systemSettings')
    filterRules.rules = await saveFilterRules(rules)
    filterRules.dialogVisible = false
    ElMessage.success('已保存')
  } catch { ElMessage.error('保存失败') }
}

async function deleteFilterRule(index: number) {
  const name = filterRules.rules[index]?.name || ''
  await ElMessageBox.confirm(`确定删除过滤词规则「${name}」？`, '删除确认', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' })
  const rules = filterRules.rules.filter((_, i) => i !== index)
  try {
    const { saveFilterRules } = await import('@/api/systemSettings')
    filterRules.rules = await saveFilterRules(rules)
    ElMessage.success('已删除')
  } catch { ElMessage.error('删除失败') }
}

async function saveTMDB() {
  if (!canWrite.value) return
  tmdb.saving = true
  try {
    const payload: any = {
      language: tmdb.language ? String(tmdb.language).trim() : null,
      poster_language: tmdb.posterLanguage ? String(tmdb.posterLanguage).trim() : null,
    }
    const apiKey = String(tmdb.apiKeyInput || '').trim()
    if (apiKey) payload.api_key = apiKey
    const data = await patchTMDBConfig(payload)
    tmdb.apiKeyInput = ''
    applyTMDBConfig(data)
    ElMessage.success('已保存')
  } finally {
    tmdb.saving = false
  }
}

function openCreate() {
  dialog.visible = true
  dialog.submitting = false
  dialog.isEdit = false
  dialog.keyLocked = false
  dialog.form.key = '$'
  dialog.form.label = null
  dialog.form.enabled = true
  dialog.form.pattern = ''
  dialog.form.replace = ''
}

function openEdit(rule: MagicRegexRuleSetting) {
  dialog.visible = true
  dialog.submitting = false
  dialog.isEdit = true
  dialog.keyLocked = true
  dialog.form.key = rule.key
  dialog.form.label = rule.label ?? null
  dialog.form.enabled = Boolean(rule.enabled)
  dialog.form.pattern = rule.pattern || ''
  dialog.form.replace = rule.replace || ''
}

async function submit() {
  const key = normalizeKey(dialog.form.key)
  if (!isValidKey(key)) {
    ElMessage.warning('key 必须以 $ 开头，且不能包含空格（最长 64）')
    return
  }
  if (!dialog.isEdit) {
    if (!String(dialog.form.pattern || '').trim()) {
      ElMessage.warning('新增规则时 pattern 不能为空')
      return
    }
  }
  dialog.submitting = true
  try {
    const data = await upsertMagicRegexRule(key, {
      label: dialog.form.label ? String(dialog.form.label).trim() : null,
      enabled: Boolean(dialog.form.enabled),
      pattern: String(dialog.form.pattern || '').trim() || null,
      replace: String(dialog.form.replace || ''),
    })
    rules.value = data.rules || []
    dialog.visible = false
    ElMessage.success('已保存')
  } finally {
    dialog.submitting = false
  }
}

async function removeRule(rule: MagicRegexRuleSetting) {
  if (!canWrite.value) return
  const title = rule.built_in ? '恢复默认规则' : '删除自定义规则'
  const message = rule.built_in
    ? `将清除对 ${rule.key} 的覆盖配置，恢复为系统默认值。`
    : `将删除 ${rule.key} 规则。`
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' })
  } catch {
    return
  }
  const data = await deleteMagicRegexRule(rule.key)
  rules.value = data.rules || []
  ElMessage.success('已更新')
}

const builtinRules = computed(() => rules.value.filter((r) => r.built_in))
const customRules = computed(() => rules.value.filter((r) => !r.built_in))

async function loadTemplates() {
  templates.loading = true
  try {
    templates.list = await fetchTaskTemplates()
  } finally {
    templates.loading = false
  }
}

function openTemplateCreate() {
  templates.editingId = null
  templates.form = { name: '', config: {} }
  templates.magicRegex.selectedKey = ''
  templates.dialogVisible = true
  loadMagicRegexRules()
  loadFilterWordsOptions()
}

function openTemplateEdit(template: TaskTemplate) {
  templates.editingId = template.id
  templates.form = { name: template.name, config: { ...template.config } }
  const patternKey = template.config.pattern || ''
  const filterRuleName = template.config.filter_rule_name || ''
  templates.magicRegex.selectedKey = ''
  templates.dialogVisible = true
  // 加载内置规则和关键词预设
  Promise.all([loadMagicRegexRules(), loadFilterWordsOptions()]).then(() => {
    // 如果 pattern 是 key（以 $ 开头），选中对应的内置规则并填充实际正则
    if (patternKey.startsWith('$')) {
      templates.magicRegex.selectedKey = patternKey
      const rule = templates.magicRegex.rules.find(r => r.key === patternKey)
      if (rule) {
        templates.form.config.pattern = rule.pattern || ''
        templates.form.config.replace = rule.replace || ''
      }
    }
    // 如果有 filter_rule_name，填充关键词显示
    if (filterRuleName) {
      const opt = templates.filterWords.options.find(o => o.name === filterRuleName)
      if (opt) {
        templates.form.config.filter_words = opt.keywords
      }
    }
  })
}

async function loadMagicRegexRules() {
  templates.magicRegex.loading = true
  try {
    const data = await fetchMagicRegexRules()
    templates.magicRegex.rules = (data.rules || []).filter((r: any) => r.enabled !== false).map((r: any) => ({
      key: r.key,
      label: r.label || r.key,
      pattern: r.pattern || '',
      replace: r.replace || '',
    }))
  } finally {
    templates.magicRegex.loading = false
  }
}

function applyMagicRule(key: string) {
  if (!key) {
    templates.magicRegex.selectedKey = ''
    templates.form.config.pattern = ''
    templates.form.config.replace = ''
    return
  }
  const rule = templates.magicRegex.rules.find(r => r.key === key)
  if (!rule) return
  templates.magicRegex.selectedKey = key
  templates.form.config.pattern = rule.pattern || ''
  templates.form.config.replace = rule.replace || ''
}

function onFilterRuleSelect(name: string) {
  if (!name) {
    templates.form.config.filter_words = ''
    return
  }
  const opt = templates.filterWords.options.find(o => o.name === name)
  if (opt) {
    templates.form.config.filter_words = opt.keywords
  }
}

async function loadFilterWordsOptions() {
  try {
    const { fetchFilterRules } = await import('@/api/systemSettings')
    const data = await fetchFilterRules()
    templates.filterWords.options = data || []
  } catch {
    templates.filterWords.options = []
  }
}

function toggleKeywordMode(key: string) {
  const modeMap: Record<string, string> = {
    'search_filter': 'search_filter_mode',
    'search_exclude': 'search_exclude_mode',
    'folder_filter': 'folder_filter_mode',
    'folder_exclude': 'folder_exclude_mode',
    'folder_priority': 'folder_priority_mode',
    'file_filter': 'file_filter_mode',
  }
  const modeKey = modeMap[key]
  if (!modeKey) return
  
  const currentMode = templates.form.config[modeKey] || ''
  if (key.includes('exclude')) {
    templates.form.config[modeKey] = currentMode === 'all' ? '' : 'all'
  } else {
    templates.form.config[modeKey] = currentMode === 'any' ? '' : 'any'
  }
}

function getKeywordModeLabel(key: string): string {
  const modeMap: Record<string, string> = {
    'search_filter': 'search_filter_mode',
    'search_exclude': 'search_exclude_mode',
    'folder_filter': 'folder_filter_mode',
    'folder_exclude': 'folder_exclude_mode',
    'folder_priority': 'folder_priority_mode',
    'file_filter': 'file_filter_mode',
  }
  const modeKey = modeMap[key]
  if (!modeKey) return '包含所有'
  
  const currentMode = templates.form.config[modeKey] || ''
  if (key.includes('exclude')) {
    return currentMode === 'all' ? '包含所有' : '包含任意'
  } else {
    return currentMode === 'any' ? '包含任意' : '包含所有'
  }
}

async function submitTemplate() {
  if (!templates.form.name.trim()) return ElMessage.warning('请输入模板名称')
  // 如果选择了内置规则，保存 key 而不是实际正则
  const configToSave = { ...templates.form.config }
  if (templates.magicRegex.selectedKey) {
    configToSave.pattern = templates.magicRegex.selectedKey
  }
  try {
    if (templates.editingId) {
      await updateTaskTemplate(templates.editingId, { name: templates.form.name, config: configToSave })
      ElMessage.success('模板已更新')
    } else {
      await createTaskTemplate({ name: templates.form.name, config: configToSave })
      ElMessage.success('模板已创建')
    }
    templates.dialogVisible = false
    await loadTemplates()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function removeTemplate(template: TaskTemplate) {
  try {
    await ElMessageBox.confirm(`确定删除模板"${template.name}"？`, '删除模板', { type: 'warning' })
    await deleteTaskTemplate(template.id)
    ElMessage.success('已删除')
    await loadTemplates()
  } catch {}
}

onMounted(() => {
  refresh()
  refreshTMDB()
  loadSharerFilter()
  loadFilterRules()
  loadTemplates()
  loadDramaLeadIn()
})
</script>

<template>
  <div class="shell-page">
    <div class="section-header">
            <div class="section-header__title">
        <h2>系统设置</h2>
        <div class="section-header__desc">任务模板、魔法重命名、搜索源与缓存策略集中配置。</div>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="保存规则" name="magic_regex">
        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>任务模板</div>
              <el-button type="primary" size="small" :disabled="!canWrite" @click="openTemplateCreate">新增模板</el-button>
            </div>
          </template>
          <el-table :data="templates.list" :loading="templates.loading" style="width: 100%">
            <el-table-column prop="name" label="模板名称" />
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button size="small" :disabled="!canWrite" @click="openTemplateEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" :disabled="!canWrite" @click="removeTemplate(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <div class="page__hint">
          <div>新增的规则 key 需要以 $ 开头（例如：$MY_RULE）。在追剧任务里将 pattern 设置为该 key，即可使用系统保存规则。</div>
          <div>replace 为默认模板；任务里 replace 留空时，会自动使用该默认值。</div>
        </div>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>内置规则</div>
              <el-button text :loading="loading" @click="refresh">刷新</el-button>
            </div>
          </template>
          <el-table :data="builtinRules" :loading="loading" style="width: 100%">
            <el-table-column prop="key" label="key" width="180" />
            <el-table-column prop="label" label="名称" min-width="160" />
            <el-table-column label="状态" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.overridden" type="warning">已覆盖</el-tag>
                <el-tag v-else type="info">默认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" :disabled="!canWrite" @click="openEdit(row)">编辑</el-button>
                <el-button size="small" :disabled="!canWrite || !row.overridden" @click="removeRule(row)">恢复默认</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>自定义规则</div>
              <el-button type="primary" size="small" :disabled="!canWrite" @click="openCreate">新增规则</el-button>
            </div>
          </template>
          <el-table :data="customRules" :loading="loading" style="width: 100%">
            <el-table-column prop="key" label="key" width="180" />
            <el-table-column prop="label" label="名称" min-width="160" />
            <el-table-column label="操作" width="160">
              <template #default="{ row }">
                <el-button size="small" :disabled="!canWrite" @click="openEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" :disabled="!canWrite" @click="removeRule(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>关键词过滤规则</div>
              <el-button type="primary" size="small" :disabled="!canWrite" @click="openFilterRuleDialog(-1)">新增规则</el-button>
            </div>
          </template>
          <el-table :data="filterRules.rules" :loading="filterRules.loading" style="width: 100%">
            <el-table-column prop="name" label="名称" />
            <el-table-column label="操作" width="160">
              <template #default="{ $index }">
                <el-button size="small" :disabled="!canWrite" @click="openFilterRuleDialog($index)">编辑</el-button>
                <el-button size="small" type="danger" :disabled="!canWrite" @click="deleteFilterRule($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-dialog v-model="filterRules.dialogVisible" :title="filterRules.editIndex >= 0 ? '编辑过滤词规则' : '新增过滤词规则'" width="480px" append-to-body>
          <el-form label-position="top">
            <el-form-item label="名称">
              <el-input v-model="filterRules.form.name" placeholder="如：综艺、动漫" />
            </el-form-item>
            <el-form-item label="过滤词">
              <el-input v-model="filterRules.form.keywords" placeholder="用 | 分隔，如：纯享版|会员版|花絮" />
              <div class="form-item-hint">文件名包含任意过滤词的文件不会被转存</div>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="filterRules.dialogVisible = false">取消</el-button>
            <el-button type="primary" :disabled="!canWrite" @click="saveFilterRule">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="搜索过滤" name="resource_search">
        <div class="page__hint">
          <div>这里设置搜索结果的分享者过滤。API 和 Python 搜索源请到“搜索源管理”页面配置。</div>
        </div>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>分享者过滤</div>
              <el-button text :loading="sharerFilter.loading" @click="loadSharerFilter">刷新</el-button>
            </div>
          </template>
          <div style="margin-bottom: 12px; color: var(--el-text-color-secondary); font-size: 13px">
            用于资源搜索结果的过滤和高亮显示。多个昵称用竖线 <code>|</code> 分隔。
          </div>
          <el-form label-width="140px" label-position="left">
            <el-form-item label="优选分享者">
              <el-button @click="sharerDialog.type = 'preferred'; sharerDialog.visible = true">
                管理名单（{{ sharerFilter.preferred.length }} 人）
              </el-button>
            </el-form-item>
            <el-form-item label="屏蔽分享者">
              <el-button @click="sharerDialog.type = 'blocked'; sharerDialog.visible = true">
                管理名单（{{ sharerFilter.blocked.length }} 人）
              </el-button>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="sharerFilter.saving" :disabled="!canWrite" @click="saveSharerFilter">保存配置</el-button>
            </el-form-item>
          </el-form>
          <!-- 分享者名单弹窗 -->
          <el-dialog
            v-model="sharerDialog.visible"
            :title="sharerDialog.type === 'preferred' ? '优选分享者名单' : '屏蔽分享者名单'"
            width="420px"
            append-to-body
          >
            <div class="sharer-tag-list">
              <div style="display: flex; gap: 8px; margin-bottom: 12px">
                <el-input v-model="sharerDialog.inputValue" placeholder="名称，多个用 | 分隔" size="small" @keyup.enter="addSharerFromInput" />
                <el-button type="primary" size="small" :disabled="!sharerDialog.inputValue?.trim()" @click="addSharerFromInput">添加</el-button>
              </div>
              <el-tag
                v-for="name in (sharerDialog.type === 'preferred' ? sharerFilter.preferred : sharerFilter.blocked)"
                :key="name"
                :type="sharerDialog.type === 'preferred' ? 'success' : 'danger'"
                closable
                :disabled="!canWrite"
                @close="removeSharer(name)"
              >{{ name }}</el-tag>
              <span v-if="!(sharerDialog.type === 'preferred' ? sharerFilter.preferred : sharerFilter.blocked).length" class="sharer-tag-empty">
                暂无，可手动输入添加或在搜索结果中点击分享者名称添加
              </span>
            </div>
          </el-dialog>
        </el-card>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>验证与缓存</div>
            </div>
          </template>
          <el-form label-width="140px" label-position="left">
            <el-form-item label="验证并行数">
              <el-input-number v-model="sharerFilter.validate_batch_size" :min="1" :max="20" :disabled="!canWrite" />
              <div class="form-item-hint">每批同时验证的链接数量，默认 5。越大验证越快，但对网盘账号压力越大。</div>
            </el-form-item>
            <el-form-item label="链接验证缓存（秒）">
              <el-input-number v-model="sharerFilter.preview_cache_ttl_seconds" :min="30" :max="3600" :disabled="!canWrite" />
              <div class="form-item-hint">分享链接有效性校验结果的缓存时长，默认 300 秒。搜索验证、自动换链都会复用它。</div>
            </el-form-item>
            <el-form-item label="搜索缓存（秒）">
              <el-input-number v-model="sharerFilter.search_cache_ttl_seconds" :min="1" :max="3600" :disabled="!canWrite" />
              <div class="form-item-hint">「深度搜索」结果的缓存时长，默认 3 秒。调大可以少打搜索源，调小更实时。</div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="sharerFilter.saving" :disabled="!canWrite" @click="saveSharerFilter">保存配置</el-button>
            </el-form-item>
          </el-form>
        </el-card>

      </el-tab-pane>

      <el-tab-pane label="TMDB" name="tmdb">
        <div class="page__hint">
          <div>用于影视发现页的 TMDB 信息补全与搜索。</div>
          <div>API Key 不回显；留空保存表示不修改。</div>
        </div>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>TMDB 配置</div>
              <div>
                <el-button text :loading="tmdb.loading" @click="refreshTMDB">刷新</el-button>
                <el-button type="primary" :loading="tmdb.saving" :disabled="!canWrite" @click="saveTMDB">保存</el-button>
              </div>
            </div>
          </template>

          <el-form label-position="top" :disabled="tmdb.loading">
            <el-form-item label="API Key（留空不修改）">
              <el-input v-model="tmdb.apiKeyInput" type="password" show-password :placeholder="tmdb.hasApiKey ? '已配置（留空不修改）' : '请输入 TMDB API Key'" />
            </el-form-item>
            <el-form-item label="语言（language）">
              <el-select v-model="tmdb.language" style="width: 260px">
                <el-option label="中文（zh-CN）" value="zh-CN" />
                <el-option label="英文（en-US）" value="en-US" />
              </el-select>
            </el-form-item>
            <el-form-item label="海报语言（poster_language）">
              <el-select v-model="tmdb.posterLanguage" style="width: 260px">
                <el-option label="中文（zh-CN）" value="zh-CN" />
                <el-option label="原始语言（original）" value="original" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="引流文件" name="drama_lead_in">
        <div class="page__hint">
          <div>引流文件为手动操作：在追剧任务的「引流文件」入口先预览网盘、目标账号、任务保存目录与素材文件，确认后才把选中的素材文件保存到任务目录。定时追更、手动执行与生成分享链接都不会自动添加。</div>
          <div>每个网盘可选择素材来源：分享链接或本账号文件（需填写素材账号与账号内完整路径）；每个网盘一个素材文件，分享内含多个文件时必须指定其中一个。开关与网盘配置使用同一个「保存」按钮，提取码留空表示保持原值。</div>
        </div>

        <el-card class="page__card" shadow="never">
          <template #header>
            <div class="card__header">
              <div>引流文件</div>
              <div>
                <el-button text :loading="dramaLeadIn.loading" @click="loadDramaLeadIn">刷新</el-button>
                <el-button type="primary" :loading="dramaLeadIn.saving" :disabled="!canWrite" @click="saveDramaLeadIn">保存</el-button>
              </div>
            </div>
          </template>

          <el-form label-position="top" :disabled="dramaLeadIn.loading">
            <el-form-item label="启用引流文件">
              <div class="lead-in-toggle">
                <el-switch v-model="dramaLeadIn.enabled" :disabled="!canWrite" />
                <span class="lead-in-toggle__hint">关闭后任务执行时不会生成引流文件，已有配置会保留。</span>
              </div>
            </el-form-item>
          </el-form>

          <div v-loading="dramaLeadIn.loading">
            <el-empty v-if="!dramaLeadIn.loading && !dramaLeadIn.drives.length" description="暂无网盘配置" :image-size="80" />

            <el-collapse v-model="dramaLeadIn.activeDrive" accordion class="lead-in-collapse">
              <el-collapse-item
                v-for="drive in dramaLeadIn.drives"
                :key="drive.drive_type"
                :name="drive.drive_type"
                class="lead-in-drive"
              >
                <template #title>
                  <div class="lead-in-drive__title">
                    <DriveTypeIcon :type="drive.drive_type" :size="20" />
                    <span>{{ drive.drive_name || drive.drive_type }}</span>
                    <el-tag v-if="!drive.supported" type="danger" size="small" effect="plain">不支持</el-tag>
                    <el-tag v-else-if="drive.configured" type="success" size="small" effect="plain">已配置</el-tag>
                    <el-tag v-else type="info" size="small" effect="plain">未配置</el-tag>
                    <span class="lead-in-drive__summary">{{ leadInCollapseSummary(drive) }}</span>
                  </div>
                </template>

                <div class="lead-in-drive__body">

              <div class="lead-in-drive__meta">
                <span>素材来源：{{ drive.mode === 'own_file' ? '本账号文件' : '分享链接' }}</span>
                <span v-if="drive.mode === 'own_file' && drive.materialAccount">素材账号：{{ drive.materialAccount }}</span>
                <span v-if="drive.mode === 'own_file' && drive.materialFileName">素材文件：{{ drive.materialFileName }}</span>
                <span v-if="drive.mode === 'share' && drive.shareurlMasked">当前分享：{{ drive.shareurlMasked }}</span>
                <span v-if="drive.mode === 'share'">提取码：{{ drive.passcodeSet ? '已配置' : '未配置' }}</span>
                <span v-if="drive.mode === 'share' && drive.fileName">素材文件：{{ drive.fileName }}</span>
                <span v-if="drive.updatedAt">最近保存：{{ formatDateTimeText(drive.updatedAt) }}</span>
              </div>

              <el-alert v-if="!drive.supported" type="warning" :closable="false" show-icon :title="drive.reason || '当前网盘暂不支持引流文件'" />

              <el-form v-else label-position="top" :disabled="!canWrite || drive.scanning || drive.resolving || drive.clearing">
                <el-form-item label="素材来源">
                  <el-radio-group v-model="drive.mode" :disabled="!canWrite" @change="onLeadInModeChange(drive)">
                    <el-radio-button value="share">分享链接</el-radio-button>
                    <el-radio-button value="own_file" :disabled="!drive.ownFileSupported">本账号文件</el-radio-button>
                  </el-radio-group>
                  <div v-if="!drive.ownFileSupported" class="form-item-hint">该网盘暂不支持本账号文件，只能使用分享链接。</div>
                  <div v-else-if="drive.mode === 'own_file'" class="form-item-hint">从本账号内部复制选中的单个文件；任务使用的账号必须与素材账号一致。</div>
                </el-form-item>

                <template v-if="drive.mode === 'share'">
                  <el-form-item label="分享链接">
                    <el-input
                      v-model="drive.shareurl"
                      clearable
                      :placeholder="drive.configured ? '输入新链接可替换，留空保存表示清空该网盘配置' : '请输入完整分享链接'"
                      @input="onLeadInShareurlInput(drive)"
                    />
                    <div class="form-item-hint">支持分享文件夹或单个文件；读取文件需要完整链接（掩码链接无法读取）。</div>
                  </el-form-item>
                  <el-form-item label="提取码（可选）">
                    <el-input
                      v-model="drive.passcode"
                      clearable
                      :placeholder="drive.passcodeSet ? '已配置（留空保持原值）' : '无提取码可留空'"
                      style="width: 220px"
                      @input="onLeadInPasscodeInput(drive)"
                    />
                  </el-form-item>
                  <el-form-item label="素材文件">
                    <div>
                      <el-button size="small" :loading="drive.scanning" :disabled="!canWrite" @click="scanLeadInDrive(drive)">读取文件</el-button>
                      <span class="form-item-hint" style="margin-left: 8px">
                        {{ drive.selectedFid ? `已选择：${leadInSelectedName(drive)}` : drive.scanned ? '请选择素材文件' : '未读取' }}
                      </span>
                    </div>
                    <div v-if="drive.scanned && drive.files.length" class="lead-in-files">
                      <el-radio-group v-model="drive.selectedFid" :disabled="!canWrite">
                        <el-radio v-for="file in drive.files" :key="file.fid" :value="file.fid" :disabled="file.dir" class="lead-in-file">
                          {{ file.name }}
                          <el-tag v-if="file.dir" size="small" type="info" effect="plain" style="margin-left: 8px">文件夹（不可选）</el-tag>
                        </el-radio>
                      </el-radio-group>
                    </div>
                    <div v-else-if="drive.scanned" class="form-item-hint">该分享内没有可选文件。</div>
                  </el-form-item>
                </template>

                <template v-else>
                  <el-form-item label="素材账号">
                    <el-select
                      v-model="drive.materialAccount"
                      clearable
                      filterable
                      style="width: 280px"
                      placeholder="请选择素材所属账号"
                      @change="onLeadInOwnFileInput(drive)"
                    >
                      <el-option
                        v-for="account in leadInAccountsFor(drive)"
                        :key="account.id"
                        :label="leadInAccountLabel(account)"
                        :value="account.name"
                      />
                    </el-select>
                    <div class="form-item-hint">只列出该网盘下已启用的账号；暂无可选账号时请先在「网盘账号」中添加。</div>
                  </el-form-item>
                  <el-form-item label="文件路径">
                    <div class="lead-in-path-row">
                      <el-input
                        v-model="drive.materialFilePath"
                        clearable
                        placeholder="/文件夹/文件名，需完整路径"
                        @input="onLeadInOwnFileInput(drive)"
                      />
                      <el-button
                        v-if="drive.ownFileBrowseSupported"
                        size="default"
                        :disabled="!canWrite"
                        @click="openLeadInDirPicker(drive)"
                      >
                        浏览
                      </el-button>
                    </div>
                    <div class="form-item-hint">按完整路径精确定位单个文件，不会按文件名全盘搜索。</div>
                  </el-form-item>
                  <el-form-item label="素材文件">
                    <div>
                      <el-button size="small" :loading="drive.resolving" :disabled="!canWrite" @click="resolveLeadInOwnFile(drive)">读取文件</el-button>
                      <span class="form-item-hint" style="margin-left: 8px">{{ leadInOwnFileSummary(drive) }}</span>
                    </div>
                  </el-form-item>
                </template>
              </el-form>

              <div v-if="drive.supported && (drive.configured || drive.clearing)" class="lead-in-drive__actions">
                <el-tag v-if="drive.clearing" type="danger" size="small" effect="plain">保存后将清空该网盘配置</el-tag>
                <el-button v-else text type="danger" size="small" :disabled="!canWrite" @click="clearLeadInDrive(drive)">清空配置</el-button>
              </div>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-card>
      </el-tab-pane>

    </el-tabs>

    <el-dialog v-model="dialog.visible" :title="dialog.isEdit ? '编辑规则' : '新增规则'" width="720px">
      <el-form label-position="top">
        <el-form-item label="key（以 $ 开头）">
          <el-input v-model="dialog.form.key" :disabled="dialog.keyLocked" placeholder="$MY_RULE" />
        </el-form-item>
        <el-form-item label="名称（可选）">
          <el-input v-model="dialog.form.label" placeholder="例如：综艺命名（含日期）" />
        </el-form-item>
        <el-form-item v-if="!dialog.form.key || !String(dialog.form.key).startsWith('$')" label="提示">
          <div class="page__hint">key 必须以 $ 开头。</div>
        </el-form-item>
        <el-form-item v-if="!currentIsBuiltinKey" label="启用">
          <el-switch v-model="dialog.form.enabled" :disabled="!canWrite" />
        </el-form-item>
        <el-form-item label="pattern（正则表达式）">
          <el-input v-model="dialog.form.pattern" type="textarea" :rows="4" placeholder="例如：^(?!.*纯享).*?第\\d+期.*" />
        </el-form-item>
        <el-form-item label="replace（默认替换模板）">
          <el-input v-model="dialog.form.replace" type="textarea" :rows="3" placeholder="{II}.{TASKNAME}.{DATE}.第{E}期{PART}.{EXT}" />
          <div v-if="Object.keys(variables).length" class="drawer-form__hint" style="margin-top: 8px">
            <div style="font-weight: 600; margin-bottom: 4px">可用变量：</div>
            <div style="display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; line-height: 1.8">
              <span v-for="(desc, key) in variables" :key="key"><code>{{ key }}</code> {{ desc }}</span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.submitting" :disabled="!canWrite" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 模板编辑弹窗 -->
    <el-dialog v-model="templates.dialogVisible" :title="templates.editingId ? '编辑模板' : '新增模板'" width="600px">
      <el-form label-position="top">
        <el-form-item label="模板名称">
          <el-input v-model="templates.form.name" placeholder="例如：4K HDR 追剧模板" />
        </el-form-item>
        <el-divider />
        <el-form-item v-for="field in TEMPLATE_FIELDS" :key="field.key" :label="field.label">
          <!-- 开关类型 -->
          <template v-if="field.type === 'switch'">
            <el-switch v-model="templates.form.config[field.key]" active-value="1" inactive-value="" />
          </template>
          <!-- 日期类型 -->
          <template v-else-if="field.type === 'date'">
            <el-date-picker v-model="templates.form.config[field.key]" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 100%" clearable :editable="false" />
          </template>
          <!-- 关键词类型（带模式切换） -->
          <template v-else-if="field.type === 'keyword'">
            <el-input v-model="templates.form.config[field.key]" placeholder="可选，用 | 分隔">
              <template #append>
                <el-button @click="toggleKeywordMode(field.key)">
                  {{ getKeywordModeLabel(field.key) }}
                </el-button>
              </template>
            </el-input>
          </template>
          <!-- 关键词过滤（预设选择 + 手动输入） -->
          <template v-else-if="field.type === 'filter_words'">
            <el-select v-model="templates.form.config.filter_rule_name" placeholder="选择预设规则" style="width: 100%; margin-bottom: 8px" clearable @change="onFilterRuleSelect">
              <el-option v-for="opt in templates.filterWords.options" :key="opt.name" :label="opt.name" :value="opt.name" />
            </el-select>
            <el-input v-model="templates.form.config.filter_words" placeholder="或手动输入关键词" />
          </template>
          <!-- 文本类型 -->
          <template v-else>
            <el-input v-model="templates.form.config[field.key]" :placeholder="field.key === 'min_size' ? '如：100MB' : ''" />
          </template>
          <div v-if="field.hint" class="drawer-form__hint">{{ field.hint }}</div>
        </el-form-item>
        <!-- 重命名设置 -->
        <el-divider />
        <div class="drawer-form__section-title">重命名设置</div>
        <el-form-item label="内置规则（可选）">
          <el-select v-model="templates.magicRegex.selectedKey" style="width: 100%" clearable placeholder="选择内置规则后会自动填入下方输入框" @change="applyMagicRule">
            <el-option v-for="rule in templates.magicRegex.rules" :key="rule.key" :label="rule.label ? `${rule.label}（${rule.key}）` : rule.key" :value="rule.key" />
          </el-select>
          <div class="drawer-form__hint">选择后会把默认 pattern / replace 填入输入框，可继续修改。</div>
        </el-form-item>
        <el-form-item label="匹配表达式（pattern）">
          <el-input v-model="templates.form.config.pattern" placeholder="$TV_REGEX 或正则表达式" />
        </el-form-item>
        <el-form-item label="替换表达式（replace）">
          <el-input v-model="templates.form.config.replace" placeholder="\1E\2.\3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="templates.dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!canWrite" @click="submitTemplate">保存</el-button>
      </template>
    </el-dialog>

    <DramaLeadInDirPicker
      v-model="leadInDirPicker.visible"
      :drive-type="leadInDirPicker.driveType"
      :drive-name="leadInDirPicker.driveName"
      :account-name="leadInDirPicker.accountName"
      :start-path="leadInDirPicker.startPath"
      @picked="onLeadInDirPicked"
    />
  </div>
</template>

<style scoped>
.page__hint {
  margin: 12px 0 16px;
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.7;
}
.page__card {
  margin-bottom: 16px;
}
.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.form-item-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
.sharer-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.sharer-tag-empty {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.drawer-form__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.5;
}
.lead-in-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
}
.lead-in-toggle__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
/* 网盘配置用折叠面板分组：一次只展开一个，标题行自带底色与分隔线。 */
.lead-in-collapse {
  border: 0;
}
.lead-in-collapse :deep(.el-collapse-item) {
  margin-bottom: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--el-fill-color-blank);
  overflow: hidden;
}
.lead-in-collapse :deep(.el-collapse-item:last-child) {
  margin-bottom: 0;
}
.lead-in-collapse :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 54px;
  padding: 10px 16px;
  border-bottom: 0;
  border-radius: var(--radius);
  background: var(--el-fill-color-light);
  font-size: 15px;
}
.lead-in-collapse :deep(.el-collapse-item__header.is-active) {
  border-bottom: 1px solid var(--line);
  border-radius: var(--radius) var(--radius) 0 0;
}
.lead-in-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}
.lead-in-collapse :deep(.el-collapse-item__content) {
  padding: 16px;
}
.lead-in-drive__title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}
.lead-in-drive__summary {
  margin-left: auto;
  padding-left: 12px;
  max-width: 42%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
@media (max-width: 768px) {
  .lead-in-drive__summary {
    display: none;
  }
}
.lead-in-drive__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.lead-in-drive__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 6px 0 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.lead-in-path-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 520px;
}
.lead-in-path-row .el-input {
  flex: 1;
}
.lead-in-files {
  margin-top: 8px;
  max-height: 220px;
  overflow-y: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--control-radius);
  padding: 4px 10px;
}
.lead-in-file {
  display: flex;
  align-items: center;
  padding: 6px 0;
}
.lead-in-file + .lead-in-file {
  border-top: 1px dashed var(--el-border-color-lighter);
}
:deep(.el-form-item__label) {
  font-weight: 500;
  color: var(--el-text-color-primary);
}
</style>
