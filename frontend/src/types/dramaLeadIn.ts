// 追剧任务引流文件：设置页与任务页共用的类型定义（字段与后端契约保持一致）。
// 素材来源：分享链接（share）或本账号文件（own_file）；历史配置没有 mode 时按 share 处理。
export type DramaLeadInMaterialMode = 'share' | 'own_file'

export interface DramaLeadInDrive {
  drive_type: string
  drive_name: string
  supported: boolean
  reason?: string
  configured: boolean
  shareurl?: string
  passcode_set: boolean
  file_fid?: string
  file_name?: string
  config_version: number
  updated_at?: string
  mode?: DramaLeadInMaterialMode
  own_file_supported?: boolean
  own_file_browse_supported?: boolean
  account_name?: string
  file_path?: string
  file_size?: number | null
}

export interface DramaLeadInSettings {
  enabled: boolean
  version?: number
  drives: DramaLeadInDrive[]
}

export interface DramaLeadInMaterialInput {
  drive_type: string
  mode?: DramaLeadInMaterialMode
  shareurl: string
  passcode?: string
  file_fid?: string
  file_name?: string
  account_name?: string
  file_path?: string
}

export interface DramaLeadInSettingsPayload {
  enabled: boolean
  materials: DramaLeadInMaterialInput[]
}

export interface DramaLeadInScanFile {
  fid: string
  name: string
  dir: boolean
  size?: number
  path?: string
}

export interface DramaLeadInScanResult {
  drive_type?: string
  shareurl?: string
  files: DramaLeadInScanFile[]
  selected_fid?: string
}

export interface DramaLeadInDriveInfo {
  drive_type: string
  drive_name: string
  supported: boolean
}

export interface DramaLeadInAccountInfo {
  account_name: string
  nickname?: string
}

export interface DramaLeadInDirectoryInfo {
  path: string
  fid?: string
  exists?: boolean
}

export interface DramaLeadInMaterialInfo {
  name: string
  fid?: string
  source?: string
  account_name?: string
  path?: string
}

export interface DramaLeadInOwnFile {
  fid: string
  name: string
  path: string
  size?: number | null
  md5?: string
  dir: boolean
}

export interface DramaLeadInOwnFileResolveResult {
  file: DramaLeadInOwnFile
}

export interface DramaLeadInOwnDirEntry {
  fid: string
  name: string
  dir: boolean
  size?: number | null
  path: string
}

export interface DramaLeadInOwnDir {
  path: string
  fid: string
  entries: DramaLeadInOwnDirEntry[]
}

export interface DramaLeadInPreview {
  available: boolean
  reason?: string
  drive?: DramaLeadInDriveInfo | null
  account?: DramaLeadInAccountInfo | null
  directory?: DramaLeadInDirectoryInfo | null
  material?: DramaLeadInMaterialInfo | null
  state: string
  state_message?: string
  last_added_at?: string | null
  preview_token?: string
  expires_at?: string | null
}

export interface DramaLeadInConfirmResult {
  status: string
  message?: string
  added_at?: string | null
  record_id?: number
  material?: DramaLeadInMaterialInfo | null
  directory?: DramaLeadInDirectoryInfo | null
}

export interface DramaLeadInTaskState {
  state: string
  message?: string
  last_added_at?: string | null
  material_name?: string
}

export interface TaskLeadInSummary {
  state: string
  last_added_at?: string | null
}
