/**
 * 判断键盘事件是否处于输入法（IME）组合输入状态。
 * 兼容 Safari（keyCode === 229）与标准浏览器（isComposing）。
 */
export function isImeComposing(event: KeyboardEvent | Event): boolean {
  const ke = event as KeyboardEvent
  return Boolean(ke?.isComposing || ke?.keyCode === 229)
}
