import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const progressSource = readFileSync(new URL('../src/utils/dramaEpisodeProgress.ts', import.meta.url), 'utf8')
const progressJs = ts.transpileModule(progressSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const progress = await import(`data:text/javascript;base64,${Buffer.from(progressJs).toString('base64')}`)

// Exercise the actual task-to-draft action without mounting the entire task editor.
const source = readFileSync(new URL('../src/components/tasks/DramaTaskDrawer.vue', import.meta.url), 'utf8')
const action = source.slice(source.indexOf('async function openCopywriting()'), source.indexOf('\nfunction selectSuggestion('))
const { outputText } = ts.transpileModule(action, { compilerOptions: { target: ts.ScriptTarget.ES2022 } })

async function generateDraft({ saved, detail = {}, mediaType = 'tv', detailFails = false }) {
  let draft
  const run = vm.runInNewContext(`${outputText}\nopenCopywriting`, {
    ...progress,
    state: { tmdb_id: 123, tmdb_media_type: mediaType, taskname: '测试剧', extra: { saved_latest: { episode: saved } } },
    fetchTMDBDetail: async () => { if (detailFails) throw new Error('offline'); return { data: detail } },
    loadSharedTasks: async () => [],
    categoryFromTmdb: () => '国剧',
    categoryFromSavepath: () => '国剧',
    fetchTMDBCredits: async () => [],
    props: { accounts: [] },
    detectDriveTypeByUrl: () => '',
    sessionStorage: { setItem: (_, value) => { draft = JSON.parse(value) } },
    router: { push: () => {} },
    ElMessage: { info: () => {} },
  })
  await run()
  return draft
}

test('copywriting uses saved progress even when TMDB is ahead', async () => {
  const draft = await generateDraft({ saved: 18, detail: { number_of_episodes: 40, last_episode_to_air: { season_number: 1, episode_number: 23 } } })
  assert.equal(draft.episode, '18')
})

test('missing saved progress falls back to cumulative aired episodes like calendar', async () => {
  const draft = await generateDraft({ detail: { seasons: [{ season_number: 0, episode_count: 2 }, { season_number: 1, episode_count: 10 }], last_episode_to_air: { season_number: 2, episode_number: 3 } } })
  assert.equal(draft.episode, '13')
})

test('ongoing total alone is not treated as aired progress', async () => {
  assert.equal((await generateDraft({ detail: { status: 'Returning Series', number_of_episodes: 40 } })).episode, '')
})

test('ended series uses total when no saved progress exists', async () => {
  assert.equal((await generateDraft({ detail: { status: 'Ended', number_of_episodes: 40, last_episode_to_air: { season_number: 1, episode_number: 39 } } })).episode, '40')
})

test('invalid saved progress stays unknown when TMDB is unavailable', async () => {
  for (const saved of [0, -2, 'invalid', 'Infinity', null]) {
    assert.equal((await generateDraft({ saved, detailFails: true })).episode, '')
  }
})

test('saved progress survives TMDB failure and accepts persisted numeric strings', async () => {
  assert.equal((await generateDraft({ saved: '21', detailFails: true })).episode, '21')
})

test('movies do not inherit episode progress', async () => {
  assert.equal((await generateDraft({ saved: 21, mediaType: 'movie' })).episode, '')
})

const qqComputed = copySourceForQq()

function copySourceForQq() {
  const source = readFileSync(new URL('../src/views/tasks/CopywritingView.vue', import.meta.url), 'utf8')
  return source.slice(source.indexOf('const episodeText = computed('), source.indexOf('\nfunction driveLabel('))
}

function qqTitle(draft) {
  return vm.runInNewContext(`${qqComputed}\ntitleLine.value`, {
    computed: (fn) => ({ get value() { return fn() } }),
    draft: { title: '沧元图', taskname: '', year: '2023', ...draft },
    episodeUnit: { value: '集' },
  })
}

test('QQ copy marks a completed series with all episodes', async () => {
  const draft = await generateDraft({ saved: 103, detail: { status: 'Ended', number_of_episodes: 103 } })
  assert.equal(qqTitle({ ...draft, title: '沧元图', year: '2023' }), '沧元图 (2023) 【全103集】')
})

test('QQ copy keeps updating label for ongoing, incomplete or unknown series', async () => {
  for (const options of [
    { saved: 103, detail: { status: 'Returning Series', number_of_episodes: 103 } },
    { saved: 102, detail: { status: 'Ended', number_of_episodes: 103 } },
    { saved: 103, detailFails: true },
    { saved: 103, detail: { status: 'Ended' } },
  ]) {
    const draft = await generateDraft(options)
    assert.equal(qqTitle({ ...draft, title: '沧元图', year: '2023' }), `沧元图 (2023) 【更${options.saved}集】`)
  }
  assert.equal(qqTitle({ episode: '' }), '沧元图 (2023)')
  assert.equal(qqTitle({ episode: '103' }), '沧元图 (2023) 【更103集】')
})

test('QQ completion follows existing canceled status and season total fallback', async () => {
  const draft = await generateDraft({ saved: 103, detail: { status: 'Canceled', seasons: [
    { season_number: 0, episode_count: 2 }, { season_number: 1, episode_count: 52 }, { season_number: 2, episode_count: 51 },
  ] } })
  assert.equal(qqTitle({ ...draft, title: '沧元图', year: '2023' }), '沧元图 (2023) 【全103集】')
})

const copySource = readFileSync(new URL('../src/views/tasks/CopywritingView.vue', import.meta.url), 'utf8')
const weiboComputed = copySource.slice(copySource.indexOf('const weiboLine1 = computed('), copySource.indexOf('\nconst weiboText1 = computed('))

function weiboTitle(episode) {
  return vm.runInNewContext(`${weiboComputed}\nweiboLine1.value`, {
    computed: (fn) => ({ value: fn() }),
    draft: { title: '测试剧', taskname: '', episode },
    weiboQuality: { value: '4K' },
    episodeUnit: { value: '集' },
  })
}

test('unknown progress is not advertised as episode one in Weibo copy', () => {
  assert.equal(weiboTitle(''), '测试剧 资源4K【已更】')
})

test('Weibo copy reflects the supplied or manually edited progress', () => {
  assert.equal(weiboTitle('18'), '测试剧 更18集资源4K【已更】')
  assert.equal(weiboTitle(' 19 '), '测试剧 更19集资源4K【已更】')
})
