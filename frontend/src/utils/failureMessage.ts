export function summarizeFailure(message?: string | null) {
  const raw = String(message || '').trim()
  if (!raw) return { summary: '执行失败，暂未提供具体原因', detail: '' }

  // 优先取网盘返回的原因，避免被请求 ID、调试 metadata 等信息干扰。
  const match = raw.match(/"(?:message|msg|errmsg|error_msg)"\s*:\s*("(?:\\.|[^"\\])*")/)
  let reason = ''
  if (match?.[1]) {
    try { reason = String(JSON.parse(match[1])).trim() } catch { /* 保留外层报错 */ }
  }
  if (!reason) {
    reason = raw.replace(/^阶段\s*=\s*[^：:\s]+\s*[：:]\s*/, '')
      .split(/\s+resp(?:onse)?\s*=/i)[0] || ''
    reason = reason.replace(/^[a-z_]+\s+failed\s*[:：]\s*/i, '').trim()
  }

  let summary = reason
  if (/已取消(?:了)?分享|分享.*(?:已取消|已被取消)/.test(reason)) summary = '原分享已被取消'
  else if (/分享.*(?:已失效|已过期|不存在)|链接.*(?:已失效|已过期)/.test(reason)) summary = '分享链接已失效或过期'
  else if (/提取码.*(?:错误|不正确)|密码.*(?:错误|不正确)/.test(reason)) summary = '分享提取码错误'
  else if (/cookie.*(?:失效|过期)|登录.*(?:失效|过期)|未登录/i.test(reason)) summary = '网盘登录状态已失效'
  else if (/timed?\s*out|timeout|超时/i.test(reason)) summary = '请求超时，暂时无法读取数据'
  else if (/connection.*(?:refused|reset|aborted)|NameResolutionError|无法连接|连接失败/i.test(reason)) summary = '网络连接失败'
  else if (/空间不足|容量不足|空间已满/.test(reason)) summary = '网盘剩余空间不足'
  else if (!reason || /^[{[]/.test(reason) || /Traceback|metadata|request_id|req_id/.test(reason)) summary = '请求失败，请展开查看详细错误'
  summary = summary.replace(/\s+/g, ' ').trim()
  if (summary.length > 100) summary = `${summary.slice(0, 100)}…`
  return { summary, detail: summary === raw ? '' : raw }
}

export async function readTaskRunError(response: Response): Promise<string> {
  const text = await response.text().catch(() => '')
  try {
    const body = JSON.parse(text)
    if (typeof body?.message === 'string' && body.message) return body.message
  } catch {
    // An intermediary can return a plain-text error.
  }
  return text || `HTTP ${response.status}`
}

export function failureStageLabel(stage: string) {
  const labels: Record<string, string> = {
    start: '开始执行', select_account: '选择网盘账号', validate_account: '验证网盘账号',
    validate_schedule: '检查执行时间', share_parse: '解析分享链接', get_stoken: '读取分享链接',
    ensure_dest_dir: '准备保存目录', fetch_share_items: '读取分享文件', plan_transfer: '准备转存',
    save_file: '转存文件', move_files: '移动文件', rename: '重命名文件',
    execute_drama: '执行追剧任务', execute_generic: '执行转存任务', end: '执行结束',
    generated_share_check: '分享链接检测失败',
  }
  return labels[stage] || stage
}
