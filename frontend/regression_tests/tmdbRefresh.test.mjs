import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

function loadModule(path, dependencies) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  })
  const exports = {}
  vm.runInNewContext(outputText, {
    exports,
    require: (name) => {
      assert.ok(name in dependencies, `unexpected dependency ${name}`)
      return dependencies[name]
    },
  })
  return exports
}

function makeCacheApis(http) {
  const media = loadModule('../src/api/media.ts', { '@/api/http': { http } })
  const store = loadModule('../src/stores/tmdbDetailCache.ts', { vue: { reactive: (obj) => obj }, '@/api/media': media })
  const management = loadModule('../src/api/tmdbCache.ts', {
    '@/api/http': { http }, '@/api/media': media, '@/stores/tmdbDetailCache': store,
  })
  return { media, store, management }
}

test('forced refresh invalidates both detail caches for the refreshed series', async () => {
  let episode = 1
  const { store, management } = makeCacheApis({
    get: async () => ({ data: { episode } }),
    post: async () => {
      episode = 3
      return { data: { queued: false, status: { exists: true } } }
    },
  })
  assert.equal((await store.ensureTmdbDetail(307853)).episode, 1)
  await management.refreshTMDBCache({ media_type: 'tv', tmdb_id: 307853, force: true })
  assert.equal((await store.ensureTmdbDetail(307853)).episode, 3)
})

test('a detail request started before refresh cannot repopulate stale data', async () => {
  let resolveOld
  let count = 0
  const { store, management } = makeCacheApis({
    get: () => ++count === 1
      ? new Promise((resolve) => { resolveOld = resolve })
      : Promise.resolve({ data: { episode: 3 } }),
    post: async () => ({ data: { queued: false, status: { exists: true } } }),
  })
  const oldRequest = store.ensureTmdbDetail(307853)
  await management.refreshTMDBCache({ media_type: 'tv', tmdb_id: 307853, force: true })
  resolveOld({ data: { episode: 1 } })
  await oldRequest
  assert.equal((await store.ensureTmdbDetail(307853)).episode, 3)
})

test('single-series refresh coalesces requests and preserves other series', async () => {
  const calls=[]
  let resolveRefresh
  const {store}=makeCacheApis({get:async(url,config)=>{
    calls.push({url,config})
    if(config?.params?.force)return new Promise(resolve=>{resolveRefresh=resolve})
    return {data:{media_type:'tv',data:{id:Number(url.split('/').pop()),name:'cached'}}}
  }})
  await store.ensureTmdbDetail(1);await store.ensureTmdbDetail(2)
  const one=store.refreshTmdbDetail(1),two=store.refreshTmdbDetail(1)
  assert.equal(calls.length,3)
  assert.equal(calls[2].url,'/media/tv/1')
  assert.equal(calls[2].config.params.force,1)
  assert.equal(store.tmdbDetailsById[1].data.name,'cached')
  resolveRefresh({data:{media_type:'tv',data:{id:1,name:'fresh'}}})
  await one;await two
  assert.equal(store.tmdbDetailsById[1].data.name,'fresh')
  assert.equal(store.tmdbDetailsById[2].data.name,'cached')
})

test('failed or empty single-series refresh retains old detail, reports failure, and permits retry', async()=>{
  let mode='normal'
  const {store}=makeCacheApis({get:async()=>{
    if(mode==='fail')throw Error('TMDB unavailable')
    return {data:{media_type:'tv',data:mode==='empty'?{}:{id:1,name:mode}}}
  }})
  await store.ensureTmdbDetail(1)
  mode='fail';await assert.rejects(store.refreshTmdbDetail(1),/unavailable/)
  assert.equal(store.tmdbDetailsById[1].data.name,'normal')
  mode='empty';await assert.rejects(store.refreshTmdbDetail(1))
  assert.equal(store.tmdbDetailsById[1].data.name,'normal')
  mode='fresh';await store.refreshTmdbDetail(1)
  assert.equal(store.tmdbDetailsById[1].data.name,'fresh')
})

test('an older background detail cannot replace a completed single-series refresh',async()=>{
  let resolveOld
  const {store}=makeCacheApis({get:(url,config)=>config?.params?.force
    ? Promise.resolve({data:{media_type:'tv',data:{id:1,name:'fresh'}}})
    : new Promise(resolve=>{resolveOld=resolve})})
  const old=store.ensureTmdbDetail(1)
  await store.refreshTmdbDetail(1)
  resolveOld({data:{media_type:'tv',data:{id:1,name:'old'}}});await old
  assert.equal(store.tmdbDetailsById[1].data.name,'fresh')
})
