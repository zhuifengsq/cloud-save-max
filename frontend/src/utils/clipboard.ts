/**
 * 复制文本到剪贴板。
 * 优先 Clipboard API（仅 HTTPS/localhost 可用），
 * 非安全上下文（http）自动降级到 textarea + execCommand。
 */
export async function copyText(text: string): Promise<boolean> {
  const value = String(text ?? '')
  if (!value) return false

  const fallback = (): boolean => {
    try {
      const textarea = document.createElement('textarea')
      textarea.value = value
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.top = '0'
      textarea.style.left = '0'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      textarea.setSelectionRange(0, value.length)
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      return ok
    } catch {
      return false
    }
  }

  if (typeof window !== 'undefined' && window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      return fallback()
    }
  }
  return fallback()
}
