let version = 0
const listeners = new Set<() => void>()

// 任务数据的版本号：任何新增/修改/删除/启停/执行成功都会 +1，
// 依赖任务列表的页面（如追剧日历）用它判断“离开期间数据是否变了”。
export function markTasksChanged() {
  version += 1
  listeners.forEach((fn) => {
    try {
      fn()
    } catch {
      // 监听方出错不影响发起变更的调用方
    }
  })
}

export function getTasksVersion() {
  return version
}

export function subscribeTasksChanged(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
