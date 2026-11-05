import { http } from './http'

export type WpsHotUpdates = {
  date: string
  entries: { title: string; links: { drive: string; url: string }[] }[]
}

export async function readWpsHotUpdates(documentUrl: string): Promise<WpsHotUpdates> {
  const { data } = await http.post<WpsHotUpdates>(
    '/tools/wps-hot-updates',
    { document_url: documentUrl },
    { headers: { 'X-Silent-Toast': '1' } },
  )
  return data
}

export async function clearWpsHotUpdatesCache(documentUrl: string): Promise<void> {
  await http.post('/tools/wps-hot-updates/clear-cache', { document_url: documentUrl }, {
    headers: { 'X-Silent-Toast': '1' },
  })
}
