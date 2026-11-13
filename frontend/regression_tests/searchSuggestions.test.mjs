import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/components/tasks/DramaTaskDrawer.vue', import.meta.url), 'utf8')
const fn = source.slice(source.indexOf('async function searchSuggestions('), source.indexOf('\nfunction openAuthorMenu('))
const js = ts.transpileModule(fn, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
function setup(fetch) {
  const suggestions = { focused: true, runId: 0, items: [{shareurl: 'old'}], notice: '', loading: false, verifying: false }
  const state = { taskname: 'movie', account_choice: 'account', addition: {} }
  const search = new Function('props', 'taskSuggestions', 'state', 'sanitizeSuggestionQuery', 'ElMessage', 'driveTypeForAccountName', 'fetchTaskSuggestions', 'verifySuggestions', js + '; return searchSuggestions')(
    {modelValue: true}, suggestions, state, x => x, {warning(){}}, () => 'quark', fetch, () => {},
  )
  return {suggestions, state, search}
}

test('failed search retains input, stops loading, clears stale results and permits retry', async () => {
  let fail = true
  const {suggestions, state, search} = setup(async () => {
    if (fail) throw new Error('secret')
    return {success: true, data: [], message: '未找到结果', partial: false, warnings: []}
  })
  await search(1)
  assert.equal(state.taskname, 'movie')
  assert.equal(suggestions.loading, false)
  assert.equal(suggestions.verifying, false)
  assert.equal(suggestions.visible, true)
  assert.equal(suggestions.error, true)
  assert.deepEqual(suggestions.items, [])
  assert.match(suggestions.notice, /搜索失败/)
  assert.doesNotMatch(suggestions.notice, /secret|未搜索到资源/)
  fail = false
  await search(1)
  assert.equal(suggestions.error, false)
  assert.equal(suggestions.notice, '未找到结果')
})

test('partial search displays degradation notice even with successful empty source', async () => {
  const {suggestions, search} = setup(async () => ({success: true, data: [], partial: true, warnings: ['safe'], message: '未找到结果'}))
  await search(1)
  assert.match(suggestions.notice, /部分来源暂不可用/)
  assert.equal(suggestions.error, false)
})
