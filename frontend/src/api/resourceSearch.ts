import { http } from '@/api/http'
import type { ApiSearchSource, ApiSearchSourceInput, PythonSearchPlugin, SearchTestResult, TaskSuggestionResponse } from '@/types/resourceSearch'

function unpackList<T>(data: T[] | { sources?: T[]; plugins?: T[]; items?: T[] }): T[] {
  if (Array.isArray(data)) return data
  return data.sources || data.plugins || data.items || []
}

export async function fetchApiSearchSources() {
  const { data } = await http.get<ApiSearchSource[] | { sources: ApiSearchSource[] }>('/resource-search/api-sources')
  return unpackList(data)
}

export async function createApiSearchSource(payload: ApiSearchSourceInput) {
  const { data } = await http.post<ApiSearchSource>('/resource-search/api-sources', payload)
  return data
}

export async function patchApiSearchSource(id: string | number, payload: Partial<ApiSearchSourceInput>) {
  const { data } = await http.patch<ApiSearchSource>(`/resource-search/api-sources/${encodeURIComponent(String(id))}`, payload)
  return data
}

export async function deleteApiSearchSource(id: string | number) {
  await http.delete(`/resource-search/api-sources/${encodeURIComponent(String(id))}`)
}

export async function testApiSearchSource(id: string | number, keyword: string) {
  const { data } = await http.post<SearchTestResult>(`/resource-search/api-sources/${encodeURIComponent(String(id))}/test`, { keyword }, { timeout: 30000 })
  return data
}

export async function fetchPythonSearchPlugins() {
  const { data } = await http.get<PythonSearchPlugin[] | { plugins: PythonSearchPlugin[] }>('/resource-search/plugins')
  return unpackList(data)
}

export async function refreshPythonSearchPlugins() {
  const { data } = await http.post<PythonSearchPlugin[] | { plugins: PythonSearchPlugin[] }>('/resource-search/plugins/refresh')
  return unpackList(data)
}

export async function patchPythonSearchPlugin(key: string, enabled: boolean) {
  const { data } = await http.patch<PythonSearchPlugin>(`/resource-search/plugins/${encodeURIComponent(key)}`, { enabled })
  return data
}

export async function testPythonSearchPlugin(key: string, keyword: string) {
  const { data } = await http.post<SearchTestResult>(`/resource-search/plugins/${encodeURIComponent(key)}/test`, { keyword }, { timeout: 30000 })
  return data
}

export async function uploadPythonSearchPlugin(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post('/resource-search/plugins/upload', form, { timeout: 30000 })
  return data
}

export async function fetchTaskSuggestions(q: string, d: number, drive_type?: string | null, search_filter?: string, search_exclude?: string, search_date_from?: string, search_filter_mode?: string, search_exclude_mode?: string, show_blocked?: boolean) {
  const params: any = { q, d }
  const dt = String(drive_type || '').trim()
  if (dt) params.drive_type = dt
  const sf = String(search_filter || '').trim()
  if (sf) params.sf = sf
  const se = String(search_exclude || '').trim()
  if (se) params.se = se
  const df = String(search_date_from || '').trim()
  if (df) params.df = df
  const sfm = String(search_filter_mode || '').trim()
  if (sfm) params.sfm = sfm
  const sem = String(search_exclude_mode || '').trim()
  if (sem) params.sem = sem
  if (show_blocked) params.show_blocked = true
  const { data } = await http.get<TaskSuggestionResponse>('/tasks/suggestions', { params, timeout: 60000 })
  return data
}

export async function fetchShareAuthor(shareurl: string) {
  const { data } = await http.get<{ author_name: string }>('/resource-search/share-author', { params: { shareurl }, timeout: 15000 })
  return data.author_name || ''
}
