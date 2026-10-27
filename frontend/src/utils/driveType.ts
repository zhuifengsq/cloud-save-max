export type DriveType = 'quark' | '115' | 'baidu' | 'xunlei' | 'aliyun' | 'uc' | '123pan' | 'cloud189' | 'cloud139' | 'guangya'

export type DriveTypeGlyph = 'quark' | 'baidu' | 'alibaba' | 'cloud' | 'bolt'

export type DriveTypeMeta = {
  label: string
  short: string
  color: string
  /** 有矢量图形时优先画图形，否则用简称文字 */
  glyph?: DriveTypeGlyph
}

export const DRIVE_TYPE_META: Record<DriveType, DriveTypeMeta> = {
  quark: { label: '夸克网盘', short: '夸', color: '#1E6FFF', glyph: 'quark' },
  baidu: { label: '百度网盘', short: '百', color: '#2468F2', glyph: 'baidu' },
  '115': { label: '115网盘', short: '115', color: '#1E88E5' },
  uc: { label: 'UC网盘', short: 'UC', color: '#FF7A00' },
  '123pan': { label: '123网盘', short: '123', color: '#3D7FF3' },
  cloud189: { label: '天翼云盘', short: '天', color: '#0B6BE6', glyph: 'cloud' },
  cloud139: { label: '移动云盘', short: '139', color: '#E60012' },
  aliyun: { label: '阿里云盘', short: '阿', color: '#FF6A00', glyph: 'alibaba' },
  xunlei: { label: '迅雷云盘', short: '雷', color: '#1E90FF', glyph: 'bolt' },
  guangya: { label: '光雅网盘', short: '光', color: '#7B61FF' },
}

export function detectDriveTypeByUrl(url: string): DriveType | null {
  const value = String(url || '')
  if (!value) return null
  if (/pan\.quark\.cn/i.test(value)) return 'quark'
  if (/(?:115|anxia|115cdn)\.com/i.test(value)) return '115'
  if (/pan\.baidu\.com/i.test(value)) return 'baidu'
  if (/pan\.xunlei\.com/i.test(value)) return 'xunlei'
  if (/(?:alipan|aliyundrive)\.com/i.test(value)) return 'aliyun'
  if (/drive\.uc\.cn/i.test(value)) return 'uc'
  if (/(?:123pan|123865|123684|123952|123912)\.com/i.test(value)) return '123pan'
  if (/(?:cloud|m\.cloud)\.189\.cn/i.test(value)) return 'cloud189'
  if (/(?:yun|caiyun)\.139\.com/i.test(value)) return 'cloud139'
  if (/(?:www\.|app\.)?guangyapan\.com/i.test(value)) return 'guangya'
  return null
}
