import type { DriveAccountItem } from '@/types/extensions'
import type { ShareurlUpdateLog } from '@/types/tasks'

export type CapacitySummary = {
  account_count: number
  enabled_account_count: number
  capacity_account_count: number
  warning_account_count: number
  total_used_space?: number | null
  total_space?: number | null
  usage_ratio?: number | null
}

export type CapacityOverview = {
  summary: CapacitySummary
  accounts: DriveAccountItem[]
  warning_accounts: DriveAccountItem[]
  unsupported_accounts: DriveAccountItem[]
  updated_at?: string | null
}

export type DramaSummary = {
  task_count: number
  enabled_task_count: number
  tmdb_bound_count: number
  unknown_schedule_count: number
  monthly_success_count: number
  window_days: number
  execution_total: number
  execution_success: number
  execution_failed: number
  execution_skipped: number
  success_rate?: number | null
  avg_duration_s?: number | null
}

export type DramaTrendPoint = {
  date: string
  total: number
  success: number
  failed: number
  skipped: number
  avg_duration_s?: number | null
}

export type DramaFailureItem = {
  task_id: number
  taskname: string
  status: string
  started_at: string
  stage?: string | null
  message?: string | null
}

export type DramaUpdateItem = {
  task_id: number
  started_at: string
  drive_type?: string | null
  transfer_count?: number
  new_files?: string[]
}

export type DramaUpdatesRange = 'today' | '24h' | '7d' | '30d'

export type DramaUpdateDriveItem = {
  task_id: number
  drive_type?: string | null
  account_name?: string | null
  started_at: string
  transfer_count: number
  new_files: string[]
  execution_count: number
}

export type DramaUpdateGroup = {
  drama_key: string
  tmdb_id?: number | null
  title: string
  latest_at: string
  total_transfer_count: number
  drives: DramaUpdateDriveItem[]
}

export type DramaUpdatesResponse = {
  range: DramaUpdatesRange
  since: string
  drama_count: number
  drive_update_count: number
  total_transfer_count: number
  groups: DramaUpdateGroup[]
}

export type DramaShareurlUpdateLog = ShareurlUpdateLog & {
  task_id: number
  taskname: string
}

export type DramaOverview = {
  scheduler: {
    enabled: boolean
    crontab: string
    timezone: string
  }
  summary: DramaSummary
  trend: DramaTrendPoint[]
  recent_updates: DramaUpdateItem[]
  recent_failures: DramaFailureItem[]
  shareurl_update_logs?: DramaShareurlUpdateLog[]
  updated_at?: string | null
}
