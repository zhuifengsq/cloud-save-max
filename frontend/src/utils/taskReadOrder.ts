type IndexItem = {id: number; taskname: string; drive_type: string}
const driveOrder = ['quark','baidu','aliyun','115','uc','123pan','xunlei','cloud189','cloud139','guangya']
export function orderedTaskIds(rows: IndexItem[], order: 'ascending' | 'descending', page: number, size: number): number[] {
  const rank = (drive: string) => !drive ? 99 : driveOrder.indexOf(drive) < 0 ? 98 : driveOrder.indexOf(drive)
  return [...rows].sort((a,b) => {
    const name = String(a.taskname || '').localeCompare(String(b.taskname || ''), 'zh-Hans-CN', {numeric:true,sensitivity:'base'})
    return name ? name * (order === 'descending' ? -1 : 1) : rank(a.drive_type) - rank(b.drive_type) || a.id - b.id
  }).slice((page - 1) * size, page * size).map(row => row.id)
}
