import type { TaskExecutionSummary, TaskShareLink } from '@/types/tasks'

export function findGeneratedShareCheck(link: TaskShareLink | null, executions?: TaskExecutionSummary[]) {
  if (!link) return null

  const shareId = link.share_id || ''
  const savedAt = link.saved_at || ''
  for (const execution of executions ?? []) {
    const result = execution.adapter_snapshot?.generated_share_check
    if (result?.share_id === shareId && result.saved_at === savedAt) return result
  }
  return null
}
