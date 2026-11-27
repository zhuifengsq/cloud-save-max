import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const composableSource = readFileSync(
  new URL('../src/composables/useSchedulerStatus.ts', import.meta.url),
  'utf8',
)
const navMenuSource = readFileSync(
  new URL('../src/layouts/NavMenu.vue', import.meta.url),
  'utf8',
)
const appLayoutSource = readFileSync(
  new URL('../src/layouts/AppLayout.vue', import.meta.url),
  'utf8',
)

function loadFormatSchedulerRunningTooltip() {
  const begin = composableSource.indexOf('export function formatSchedulerRunningTooltip')
  const end = composableSource.indexOf('/**\n * 轻量监听后台每日定时追剧任务')
  assert(begin >= 0 && end > begin, 'formatSchedulerRunningTooltip block must exist')
  const js = ts.transpileModule(
    composableSource.slice(begin, end).replace('export function ', 'function '),
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
  ).outputText
  return new Function(`${js}; return formatSchedulerRunningTooltip;`)()
}

test('formatSchedulerRunningTooltip formats elapsed duration cleanly', () => {
  const format = loadFormatSchedulerRunningTooltip()
  const baseMs = Date.parse('2026-11-27T15:00:00.000Z')

  assert.equal(format(null, baseMs), '定时任务执行中')
  assert.equal(format(undefined, baseMs), '定时任务执行中')
  assert.equal(format('not-a-date', baseMs), '定时任务执行中')
  assert.equal(format('2026-11-27T14:59:30.000Z', baseMs), '定时任务执行中')
  assert.equal(format('2026-11-27T14:58:00.000Z', baseMs), '定时任务执行中 · 已运行 2 分钟')
  assert.equal(format('2026-11-27T14:00:00.000Z', baseMs), '定时任务执行中 · 已运行 1 小时')
  assert.equal(format('2026-11-27T13:45:00.000Z', baseMs), '定时任务执行中 · 已运行 1 小时 15 分钟')
})

test('global scheduler indicator wires running->idle auto-refresh and navigation badges', () => {
  assert(
    composableSource.includes('if (wasRunning && !nextRunning)') &&
      composableSource.includes('markTasksChanged()'),
    'useSchedulerStatus should trigger markTasksChanged on running->idle transition',
  )
  assert(
    navMenuSource.includes('nav-running-pill') && navMenuSource.includes('nav-running-dot'),
    'NavMenu should render expanded pill and collapsed dot indicators',
  )
  assert(
    appLayoutSource.includes('useSchedulerStatus') &&
      appLayoutSource.includes('header-running-pill') &&
      appLayoutSource.includes("tab.name === 'DramaTasks'"),
    'AppLayout should pass running state to NavMenu, mobile header, and mobile tabbar',
  )
})
