import { http } from '@/api/http'
import type {
  DramaLeadInConfirmResult,
  DramaLeadInOwnDir,
  DramaLeadInOwnFileResolveResult,
  DramaLeadInPreview,
  DramaLeadInScanResult,
  DramaLeadInSettings,
  DramaLeadInSettingsPayload,
} from '@/types/dramaLeadIn'

// 错误信息由页面自行展示，统一关闭拦截器自动提示，避免重复弹出。
const SILENT_CONFIG = { headers: { 'X-Silent-Toast': '1' } }

export async function fetchDramaLeadInSettings() {
  const { data } = await http.get<DramaLeadInSettings>('/system-settings/drama-lead-in', SILENT_CONFIG)
  return data
}

export async function updateDramaLeadInSettings(payload: DramaLeadInSettingsPayload) {
  const { data } = await http.put<DramaLeadInSettings>('/system-settings/drama-lead-in', payload, SILENT_CONFIG)
  return data
}

export async function scanDramaLeadInMaterial(payload: { drive_type: string; shareurl: string; passcode?: string }) {
  const { data } = await http.post<DramaLeadInScanResult>('/system-settings/drama-lead-in/scan', payload, {
    ...SILENT_CONFIG,
    timeout: 90000,
  })
  return data
}

// 本账号文件模式：按「素材账号 + 账号内完整路径」精确定位文件，供设置页「读取文件」按钮使用。
export async function resolveDramaLeadInOwnFile(payload: {
  drive_type: string
  account_name: string
  file_path: string
}) {
  const { data } = await http.post<DramaLeadInOwnFileResolveResult>(
    '/system-settings/drama-lead-in/own-file/resolve',
    payload,
    { ...SILENT_CONFIG, timeout: 90000 },
  )
  return data
}

// 本账号文件模式：浏览账号内目录（只读），供设置页目录选择弹窗使用。
export async function listDramaLeadInOwnDir(payload: {
  drive_type: string
  account_name: string
  dir_path: string
}) {
  const { data } = await http.post<DramaLeadInOwnDir>(
    '/system-settings/drama-lead-in/own-file/list',
    payload,
    { ...SILENT_CONFIG, timeout: 90000 },
  )
  return data
}

export async function previewTaskLeadIn(taskId: number) {
  const { data } = await http.get<DramaLeadInPreview>(`/tasks/${taskId}/lead-in/preview`, SILENT_CONFIG)
  return data
}

export async function confirmTaskLeadIn(taskId: number, payload: { preview_token: string }) {
  const { data } = await http.post<DramaLeadInConfirmResult>(`/tasks/${taskId}/lead-in/confirm`, payload, {
    ...SILENT_CONFIG,
    timeout: 120000,
  })
  return data
}

type ApiErrorLike = {
  response?: {
    data?: {
      code?: string
      message?: string
      detail?: string
    }
  }
}

export function readLeadInErrorCode(error: unknown) {
  const data = (error as ApiErrorLike)?.response?.data
  return String(data?.code || '')
}

export function readLeadInErrorMessage(error: unknown, fallback: string) {
  const data = (error as ApiErrorLike)?.response?.data
  const message = String(data?.message || data?.detail || '').trim()
  return message || fallback
}
