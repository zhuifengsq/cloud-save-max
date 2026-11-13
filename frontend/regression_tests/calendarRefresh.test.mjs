import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const view=readFileSync(new URL('../src/views/tasks/DramaCalendarView.vue',import.meta.url),'utf8')
function extract(start,end){const begin=view.indexOf(start);assert(begin>=0);return ts.transpileModule(view.slice(begin,view.indexOf(end,begin)),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText}

test('calendar toolbar refresh only reads tasks and does not wait for background TMDB requests',async()=>{
  const reads=[],derived=[]
  let finishBackground
  const tasks={value:[]},loading={value:false}
  const rows=[{id:1,task_type:'drama',enabled:true,tmdb_media_type:'tv',tmdb_id:12,extra:{saved_latest:{episode:10}}}]
  const globals={tasks,loading,loadSharedTasks:async()=>{reads.push('tasks');return rows},getTasksVersion:()=>1,
    tmdbDetailIdsNeedingRefresh:ids=>ids,ensureTmdbDetails:()=>{reads.push('tmdb');return new Promise(resolve=>{finishBackground=resolve})},
    ensureDerived:id=>derived.push(id),getTmdbDetailStoreVersion:()=>1,refreshTodayKey:()=>{},TMDB_DETAIL_REFRESH_MS:1800000,
    ElMessage:{error:message=>assert.fail(message)},posterFailures:{},clearDetailCache:()=>{reads.push('clear')},invalidateTmdbDetails:()=>{reads.push('invalidate')}}
  const code=extract('async function performLoad(', '\nfunction stopAutoCheck()')+extract('async function handleRefresh()', '\n// 工作台可能')
  const actions=new Function(...Object.keys(globals),'let refreshPromise=null,lastLoadedAt=0,loadedTasksVersion=-1,derivedStoreVersion=-1;'+code+';return {refreshData,handleRefresh}')(...Object.values(globals))
  const background=actions.refreshData({silent:true})
  await Promise.resolve();await Promise.resolve()
  const local=actions.handleRefresh()
  let complete=false;local.then(()=>{complete=true})
  for(let i=0;i<10;i++)await Promise.resolve()
  assert.equal(complete,true,'local refresh must finish before TMDB does')
  assert.deepEqual(reads,['tasks','tmdb','tasks'])
  assert.equal(tasks.value[0].extra.saved_latest.episode,10)
  assert.equal(loading.value,false)
  finishBackground();await background;await local
  assert(derived.includes(12))
})
