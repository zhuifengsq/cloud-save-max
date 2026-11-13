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
      status: 'valid' | 'invalid' | 'pending' | 'error'
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
  // 追剧任务分类：国剧 / 韩剧 / 日剧 / 美剧 / 港台剧 / 综艺 / 动漫 / 电影 / 其他剧 / 未分类
  // 后端唯一口径（app/application/media_category.py）；非追剧任务为 null
  media_category?: string | null
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

// Bounded read contracts are distinct from legacy mutation/full collection responses.
export type TaskExecutionSummary = Omit<TaskExecutionItem, 'run_log' | 'tree_summary'>
export type TaskSummaryExtra = Pick<TaskExtraConfig, 'saved_latest' | 'auto_update_shareurl' | 'auto_update_115_shareurl' | 'share_link'> & {runweek?: unknown; runweek_mode?: unknown; allow_once?: unknown}
export type TaskSummaryAddition = {update_subdir?: string | null; save_subdir?: string | null; weekdays?: number[] | null}
export type TaskSummary = Omit<TaskItem, 'executions' | 'addition' | 'extra'> & { drive_type: string; executions: TaskExecutionSummary[]; addition: TaskSummaryAddition; extra: TaskSummaryExtra }
export type TaskOverview = Omit<TaskSummary, 'addition' | 'extra'> & {addition: TaskSummaryAddition; extra: TaskSummaryExtra}
export type TaskDetail = Omit<TaskSummary, 'addition' | 'extra'> & {addition: Record<string, any>; extra: TaskExtraConfig}
export type TaskFacets = { categories: string[]; drives: string[]; failed_count: number }
export type TaskPage = { items: TaskSummary[]; total: number; page: number; page_size: number; facets: TaskFacets }
export type TaskReadFilters = { q?: string; task_type?: string; enabled?: boolean; drive_type?: string; media_category?: string; failed_only?: boolean }
export type TaskPageQuery = TaskReadFilters & { page: number; page_size: number; name_order?: 'ascending' | 'descending' | '' }
export type TaskOrderIndexItem = { id: number; taskname: string; drive_type: string }
export type TaskExecutionPage = { items: TaskExecutionSummary[]; total: number; page: number; page_size: number }
export type TaskExecutionDetail = TaskExecutionSummary & {
  tree_summary: string; run_log: string; log_next_offset: number; log_complete: boolean
  log_truncated: boolean; log_dropped_bytes: number; tree_truncated: boolean
}
