import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/views/extensions/MagicRegexView.vue', import.meta.url), 'utf8')
const start = source.indexOf('function applyDramaLeadInSettings(')
const end = source.indexOf('\nfunction leadInCollapseSummary(', start)
const { outputText } = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } })

function createForm(activeDrive = '') {
  const dramaLeadIn = { enabled: false, activeDrive, drives: [] }
  const apply = vm.runInNewContext(`${outputText}\napplyDramaLeadInSettings`, { dramaLeadIn })
  return { dramaLeadIn, apply }
}

const settings = { enabled: true, drives: [
  { drive_type: 'quark', supported: true, configured: true },
  { drive_type: 'baidu', supported: true, configured: false },
] }

test('lead-in settings initially keep every drive collapsed, including configured drives', () => {
  const { dramaLeadIn, apply } = createForm()
  apply(settings)
  assert.equal(dramaLeadIn.activeDrive, '')
  assert.equal(dramaLeadIn.drives.length, 2)
  assert.equal(dramaLeadIn.enabled, true)
  assert.equal(dramaLeadIn.drives[0].configured, true)
})

test('refresh preserves a manually opened drive or a manually collapsed list', () => {
  const { dramaLeadIn, apply } = createForm('baidu')
  apply(settings)
  assert.equal(dramaLeadIn.activeDrive, 'baidu')
  dramaLeadIn.activeDrive = ''
  apply(settings)
  assert.equal(dramaLeadIn.activeDrive, '')
})

test('a removed drive closes without automatically opening another drive', () => {
  const { dramaLeadIn, apply } = createForm('removed')
  apply(settings)
  assert.equal(dramaLeadIn.activeDrive, '')
  apply({ enabled: false, drives: [] })
  assert.equal(dramaLeadIn.activeDrive, '')
})
