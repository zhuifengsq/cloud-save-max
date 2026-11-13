import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/utils/dramaEpisodeProgress.ts',import.meta.url),'utf8')
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const progress=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('calendar distinguishes confirmed saved progress from TMDB aired fallback',()=>{
  assert.equal(progress.calendarProgressText(10,16,12),'已存 10 / 16')
  assert.equal(progress.calendarProgressText(null,16,10),'已播 10 / 16')
  assert.equal(progress.calendarProgressText(null,16,null),'已存待确认')
  assert.equal(progress.calendarProgressText(10,null,12),'已存 10')
  assert.equal(progress.calendarProgressText(20,16,12),'已存 20')
})
