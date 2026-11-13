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
