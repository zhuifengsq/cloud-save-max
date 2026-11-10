import type { TaskLeadInSummary } from '@/types/dramaLeadIn'

export type TaskExtraConfig = Record<string, any> & {
  auto_update_shareurl?: boolean | null
  auto_update_115_shareurl?: boolean | null
  share_link?: TaskShareLink | null
  shareurl_update_logs?: ShareurlUpdateLog[]
  saved_latest?: {
    episode?: number | null
    season?: number | null
    name?: string | null
    at?: string | null
  } | null
}

export type ShareurlUpdateLog = {
  id: string
  at: string
  source: 'auto' | 'manual'
  status: 'success' | 'failed'
  message: string
  old_shareurl: string
  new_shareurl: string
  round?: number | null
}

export type TaskExecutionItem = {
  id: number
  task_id: number
  status: string
  started_at: string
  finished_at?: string | null
  tree_summary?: string | null
  message?: string | null
  stage?: string | null
  run_log?: string | null
  adapter_snapshot: Record<string, any> & {
    generated_share_check?: {
      share_id: string
      saved_at: string
      status: 'valid' | 'invalid' | 'error'
      message: string
      checked_at: string
    }
  }
}

export type TaskShareLink = {
  share_url: string
  share_id: string
  title: string
  drive_type: string
  account_name: string
  passcode?: string | null
  expires_in_days?: number | null
  saved_at?: string | null
}

export type TaskItem = {
  id: number
  task_uid: string
  task_type: string
  taskname: string
  shareurl: string
  savepath: string
  pattern?: string | null
  replace?: string | null
  ignore_extension: boolean
  account_name?: string | null
  tmdb_id?: number | null
  tmdb_media_type?: string | null
  tmdb_status?: string | null
  tmdb_is_ended?: boolean | null
  drama_update_progress?: {
    available: boolean
    tmdb_season?: number | null
    tmdb_episode?: number | null
    saved_season?: number | null
    saved_episode?: number | null
    behind_episodes?: number | null
    is_latest?: boolean | null
    snapshot_captured_at?: string | null
    reason?: string | null
  } | null
  enabled: boolean
  addition: Record<string, any>
  extra: TaskExtraConfig
  created_at: string
  updated_at: string
  executions?: TaskExecutionItem[]
  // 引流文件状态（由后端任务详情接口返回）
  lead_in?: TaskLeadInSummary | null
  // 运行日期判定（后端 application/drama_schedule.py）：仅用于说明「今天不在运行日期」，
  // 手动执行不受它限制（后端 RunPolicy.manual_run 会跳过日期类拦截），定时轮询才按它跳过。
  run_schedule?: TaskRunSchedule | null
}

export type TaskRunSchedule = {
  allowed: boolean
  mode: string
  weekdays: number[]
  reason: string
  message: string
}

export type TaskSchedulerSetting = {
  enabled: boolean
  crontab: string
  timezone: string
}

export type StopCompletedDramaTasksResponse = {
  checked: number
  matched: number
  stopped: number
  task_ids: number[]
}

export type SharePreviewItem = {
  fid: string
  fid_token?: string | null
  name: string
  name_re?: string | null
  is_dir: boolean
  updated_at?: any | null
  size?: number | null
  children_count?: number | null
  file_name?: string | null
  file_name_re?: string | null
  file_name_saved?: string | null
  filtered_by_size?: boolean | null
  filtered_by_keyword?: boolean | null
  dir?: boolean | null
  include_items?: number | null
}

export type SharePreviewResponse = {
  drive_type: string
  suggested_account_name?: string | null
  pwd_id?: string | null
  pdir_fid?: string | null
  share_author_name?: string | null
  items: SharePreviewItem[]
}

export type SharePreviewBatchItem = {
  shareurl: string
  drive_type?: string | null
  ok: boolean
  message?: string | null
  suggested_account_name?: string | null
  pdir_fid?: string | null
  resolved_pdir_fid?: string | null
  latest_video?: {
    fid?: string | null
    name?: string | null
    updated_at?: any | null
    size?: number | null
    season?: number | null
    episode?: number | null
  } | null
}

export type SharePreviewBatchResponse = {
  items: SharePreviewBatchItem[]
}

export type DriveBrowseItem = {
  fid: string
  name: string
  is_dir: boolean
  updated_at?: any | null
  size?: number | null
  include_items?: number | null
  file_name?: string | null
  dir?: boolean | null
}

export type DriveBrowsePath = {
  fid: string
  name: string
}

export type DriveBrowseResponse = {
  account_name: string
  drive_type?: string | null
  dir_path: string
  exists: boolean
  pdir_fid?: string | null
  items: DriveBrowseItem[]
  paths?: DriveBrowsePath[]
}

export type MagicRegexRule = {
  key: string
  label?: string | null
  pattern: string
  replace: string
}

export type MagicRegexResponse = {
  rules: MagicRegexRule[]
}
