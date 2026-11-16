import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/driveAccountConfig.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { buildAccountConfigUpdate } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('editing omits empty saved credentials and keeps public settings', () => {
  assert.deepEqual(buildAccountConfigUpdate({ cookie: '', protocol: 'https' }, [{ key: 'cookie', secret: true }], ['cookie'], [], true),
    { config: { protocol: 'https' }, clear_credentials: [] })
})
test('only explicit clear deletes existing credentials; replacements send actual input', () => {
  assert.deepEqual(buildAccountConfigUpdate({ cookie: 'fake-new' }, [], ['cookie'], [], true),
    { config: { cookie: 'fake-new' }, clear_credentials: [] })
  assert.deepEqual(buildAccountConfigUpdate({ cookie: '' }, [], ['cookie'], ['cookie'], true),
    { config: {}, clear_credentials: ['cookie'] })
})
