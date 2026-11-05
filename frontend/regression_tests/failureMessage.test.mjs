import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/failureMessage.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { summarizeFailure, failureStageLabel, readTaskRunError } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('task stream reads the Chinese message from duplicate and overload errors', async () => {
  for (const [status, code, message] of [
    [409, 'TASK_ALREADY_RUNNING', '该任务正在执行，请等待本次执行结束'],
    [429, 'TASK_CAPACITY_EXCEEDED', '正在执行的任务过多，请稍后再试'],
  ]) {
    const response = new Response(JSON.stringify({ code, message, detail: null }), { status })
    assert.equal(await readTaskRunError(response), message)
  }
  assert.equal(await readTaskRunError(new Response('upstream unavailable', { status: 502 })), 'upstream unavailable')
})

test('cancelled share hides response metadata from summary', () => {
  const raw = '阶段=get_stoken：get_stoken failed: 好友已取消了分享 resp={"status":404,"code":41012,"message":"好友已取消了分享","req_id":"abc","metadata":{"x":"long"}}'
  assert.equal(summarizeFailure(raw).summary, '原分享已被取消')
  assert.equal(summarizeFailure(raw).detail, raw)
})

test('timeouts do not claim that the share is invalid', () => {
  const result = summarizeFailure('阶段=get_stoken：HTTPSConnectionPool(host=pan.quark.cn): Read timed out. (read timeout=30)')
  assert.equal(result.summary, '请求超时，暂时无法读取数据')
})

test('extracts provider reason instead of displaying response JSON', () => {
  assert.equal(summarizeFailure('get_stoken failed resp={"status":400,"message":"提取码错误","request_id":"abc"}').summary, '分享提取码错误')
  assert.equal(summarizeFailure('fetch failed resp={"status":400,"message":"当日操作次数已达上限"}').summary, '当日操作次数已达上限')
})

test('404 alone is not treated as a cancelled share', () => {
  assert.notEqual(summarizeFailure('HTTP 404: Not Found').summary, '原分享已被取消')
})

test('plain reasons stay concise without unnecessary details', () => {
  assert.deepEqual(summarizeFailure('未搜索到候选链接'), { summary: '未搜索到候选链接', detail: '' })
})

test('unknown long errors are bounded and full text stays available', () => {
  const raw = '未知服务错误 '.repeat(100)
  const result = summarizeFailure(raw)
  assert(result.summary.length <= 101)
  assert.equal(result.detail, raw.trim())
})

test('empty error still has a readable fallback', () => {
  assert.equal(summarizeFailure('').summary, '执行失败，暂未提供具体原因')
})

test('stages are translated and unknown stages remain identifiable', () => {
  assert.equal(failureStageLabel('get_stoken'), '读取分享链接')
  assert.equal(failureStageLabel('fetch_share_items'), '读取分享文件')
  assert.equal(failureStageLabel('future_stage'), 'future_stage')
})
