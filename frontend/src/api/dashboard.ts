import { http } from '@/api/http'
import type { CapacityOverview, DramaOverview, DramaUpdatesRange, DramaUpdatesResponse } from '@/types/dashboard'

export async function fetchCapacityOverview() {
  const { data } = await http.get<CapacityOverview>('/dashboard/capacity-overview')
  return data
}

export async function fetchDramaOverview(days = 30) {
  const { data } = await http.get<DramaOverview>('/dashboard/drama-overview', { params: { days } })
  return data
}

export async function fetchDramaUpdates(range: DramaUpdatesRange = 'today') {
  const { data } = await http.get<DramaUpdatesResponse>('/dashboard/drama-updates', { params: { range } })
  return data
}

export async function clearDramaFailures(days = 0) {
  const { data } = await http.post<{ deleted: number }>('/dashboard/drama/clear-failures', null, {
    params: { days },
  })
  return data
}

export async function clearDramaShareurlLogs() {
  const { data } = await http.post<{ deleted: number }>('/dashboard/drama/clear-shareurl-logs')
  return data
}
