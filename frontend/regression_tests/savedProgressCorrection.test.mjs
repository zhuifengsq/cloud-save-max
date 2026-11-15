import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/views/tasks/DramaCalendarView.vue',import.meta.url),'utf8')
function action(){const start=source.indexOf('async function correctSavedProgress(');assert(start>=0,'missing per-task correction action');return ts.transpileModule(source.slice(start,source.indexOf('\n// 工作台可能',start)),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText}

test('correction is per task, coalesces repeated clicks, and does not request TMDB',async()=>{
 const tasks={value:[{id:1,extra:{saved_latest:{episode:30},custom:'keep'}},{id:2,extra:{saved_latest:{episode:20}}}]}
 const correctingProgress={},calls=[],messages=[];let finish
 const run=new Function('tasks','correctingProgress','correctTaskSavedProgress','refreshData','ElMessage',action()+';return correctSavedProgress')(
  tasks,correctingProgress,id=>{calls.push(id);return new Promise(resolve=>{finish=resolve})},async opts=>{assert.equal(opts.localOnly,true)},
  {success:message=>messages.push(message),error:message=>assert.fail(message)})
 const first=run(1),second=run(1)
 assert.deepEqual(calls,[1]);assert.equal(correctingProgress[1],true)
 finish({task_id:1,previous_episode:30,saved_latest:{episode:10},scanned_files:10,scanned_directories:1})
 await first;await second
 assert.equal(tasks.value[0].extra.saved_latest.episode,10)
 assert.equal(tasks.value[0].extra.custom,'keep')
 assert.equal(tasks.value[1].extra.saved_latest.episode,20)
 assert(!correctingProgress[1]);assert.equal(messages.length,1)
})

test('failed correction retains saved progress and permits retry',async()=>{
 const tasks={value:[{id:1,extra:{saved_latest:{episode:30}}}]},correctingProgress={},errors=[]
 let fail=true
 const run=new Function('tasks','correctingProgress','correctTaskSavedProgress','refreshData','ElMessage',action()+';return correctSavedProgress')(
  tasks,correctingProgress,async()=>{if(fail)throw Error('page 2 failed');return {task_id:1,saved_latest:{episode:10}}},async()=>{},
  {success:()=>{},error:message=>errors.push(message)})
 await run(1);assert.equal(tasks.value[0].extra.saved_latest.episode,30);assert(!correctingProgress[1]);assert.equal(errors.length,1)
 fail=false;await run(1);assert.equal(tasks.value[0].extra.saved_latest.episode,10)
})

test('variety files without episode numbers show an informational notice and retain progress',async()=>{
 const tasks={value:[{id:1,media_category:'综艺',extra:{saved_latest:{episode:30},custom:'keep'}}]},correctingProgress={},messages=[],errors=[]
 let attempts=0
 const run=new Function('tasks','correctingProgress','correctTaskSavedProgress','refreshData','ElMessage',action()+';return correctSavedProgress')(
  tasks,correctingProgress,async()=>{attempts++;throw {response:{data:{code:'SAVED_PROGRESS_UNRECOGNIZED',message:'无法识别集号：微醺派对.mp4'}}}},
  async()=>assert.fail('No progress changed; refresh is unnecessary'),
  {success:()=>assert.fail('Unknown episodes must not be reported as corrected'),info:message=>messages.push(message),error:message=>errors.push(message)})
 await run(1)
 assert.equal(errors.length,0)
 assert.equal(messages.length,1)
 assert.match(messages[0],/期号.*保留原进度/)
 assert.deepEqual(tasks.value[0].extra,{saved_latest:{episode:30},custom:'keep'})
 assert(!correctingProgress[1])
 await run(1)
 assert.equal(attempts,2)
})

test('read failures, scan limits and non-variety recognition errors still show errors',async()=>{
 for(const [category,code] of [['综艺','SAVED_PROGRESS_READ_FAILED'],['综艺','SAVED_PROGRESS_SCAN_LIMIT'],['国剧','SAVED_PROGRESS_UNRECOGNIZED'],[undefined,'SAVED_PROGRESS_UNRECOGNIZED']]){
  const tasks={value:[{id:1,media_category:category,extra:{saved_latest:{episode:30}}}]},correctingProgress={},errors=[]
  const run=new Function('tasks','correctingProgress','correctTaskSavedProgress','refreshData','ElMessage',action()+';return correctSavedProgress')(
   tasks,correctingProgress,async()=>{throw {response:{data:{code,message:'原已存进度保留'}}}},async()=>{},
   {success:()=>assert.fail('Unexpected success'),info:()=>assert.fail('Unexpected informational notice'),error:message=>errors.push(message)})
  await run(1)
  assert.deepEqual(errors,['原已存进度保留'])
  assert.equal(tasks.value[0].extra.saved_latest.episode,30)
  assert(!correctingProgress[1])
 }
})
