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
