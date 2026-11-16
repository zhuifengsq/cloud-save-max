import type { ConfigFieldItem } from '@/types/extensions'

export function buildAccountConfigUpdate(
  config: Record<string, any>, fields: ConfigFieldItem[], savedSecrets: string[], clear: string[], editing: boolean,
) {
  const secretKeys = new Set([...savedSecrets, ...fields.filter((field) => field.secret || field.input_type === 'password').map((field) => field.key)])
  const result = { ...config }
  if (editing) {
    for (const key of secretKeys) {
      if (result[key] === '' || result[key] == null || clear.includes(key)) delete result[key]
    }
  }
  return { config: result, clear_credentials: editing ? [...clear] : [] }
}
