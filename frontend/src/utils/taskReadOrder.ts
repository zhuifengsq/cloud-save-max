import type { TaskOrderIndexItem } from '@/types/tasks'

const driveOrder = ['quark','baidu','aliyun','115','uc','123pan','xunlei','cloud189','cloud139','guangya']
export function orderedTaskIds(rows: TaskOrderIndexItem[], order: 'ascending' | 'descending', page: number, size: number): number[] {
  const rank = (drive: string) => !drive ? 99 : driveOrder.indexOf(drive) < 0 ? 98 : driveOrder.indexOf(drive)
  return [...rows].sort((a,b) => {
    const aPin = a.pin_rank ?? Number.MAX_SAFE_INTEGER
    const bPin = b.pin_rank ?? Number.MAX_SAFE_INTEGER
    if (aPin !== bPin) return aPin - bPin
    const name = String(a.taskname || '').localeCompare(String(b.taskname || ''), 'zh-Hans-CN', {numeric:true,sensitivity:'base'})
    return name ? name * (order === 'descending' ? -1 : 1) : rank(a.drive_type) - rank(b.drive_type) || a.id - b.id
  }).slice((page - 1) * size, page * size).map(row => row.id)
}
