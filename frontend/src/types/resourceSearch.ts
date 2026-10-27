export type ApiSearchSource = {
  id: string | number
  name: string
  enabled: boolean
  method: 'GET' | 'POST'
  url: string
  headers: Record<string, string>
  query_params: Record<string, unknown>
  body: Record<string, unknown> | null
  response_rule?: string
  items_path: string
  title_path: string
  url_path: string
  password_path: string
  datetime_path: string
}

export type ApiSearchSourceInput = Omit<ApiSearchSource, 'id'>

export type PythonSearchPlugin = {
  key: string
  name: string
  description?: string
  version?: string
  enabled: boolean
  status?: string
  error?: string | null
}

export type SearchTestResult = {
  results?: TaskSuggestionItem[]
  items?: TaskSuggestionItem[]
  data?: TaskSuggestionItem[]
  count?: number
  message?: string
  error?: string | null
  success?: boolean
}

export type TaskSuggestionItem = {
  taskname: string
  shareurl: string
  content?: string | null
  datetime?: string | null
  channel?: string | null
  source?: string | null
  verify?: boolean | null
  share_author_name?: string | null
  is_preferred_sharer?: boolean
  is_blocked_sharer?: boolean
}

export type TaskSuggestionResponse = {
  success: boolean
  data: TaskSuggestionItem[]
  message?: string | null
}

