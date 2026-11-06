// 通用时间展示工具：后端返回 ISO 字符串，无效值统一回退占位符。
export function formatDateTimeText(value?: string | null, fallback = '—') {
  const raw = String(value || '').trim()
  if (!raw) return fallback
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
