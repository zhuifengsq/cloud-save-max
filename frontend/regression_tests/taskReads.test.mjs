import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

test('task name ordering is global, natural Chinese, drive priority and stable id', async () => {
  const source = readFileSync(new URL('../src/utils/taskReadOrder.ts', import.meta.url), 'utf8')
  const js = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
  const {orderedTaskIds} = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
  const rows = [{id:9,taskname:'剧10',drive_type:'quark'},{id:7,taskname:'剧2',drive_type:'baidu'},{id:6,taskname:'剧2',drive_type:'quark'},{id:5,taskname:'剧2',drive_type:'quark'}]
  assert.deepEqual(orderedTaskIds(rows,'ascending',1,2), [5,6])
  assert.deepEqual(orderedTaskIds(rows,'ascending',2,2), [7,9])
  assert.deepEqual(orderedTaskIds(rows,'descending',1,3), [9,5,6])
})

function makeCache() {
  let version=0, listener
  const calls=[]
  let overviewResolve
  const overview = new Promise(resolve => { overviewResolve=resolve })
  const source=readFileSync(new URL('../src/stores/taskCache.ts', import.meta.url),'utf8').replace(/^import .*$/gm,'').replace(/export /g,'')
  const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
  const cache=new Function('ref','fetchTaskOverview','fetchTaskPage','fetchTaskOrderIndex','getTasksVersion','subscribeTasksChanged','orderedTaskIds',js+';return {loadSharedTasks,loadTaskPage,sharedTasks,hasSharedTasks}')(
    value=>({value}),()=>{calls.push('overview');return overview},async params=>{calls.push(params);return {items:[],total:45,page:params.page,page_size:params.page_size,facets:{}}},async filters=>{calls.push({index:filters});return Array.from({length:45},(_,i)=>({id:i+1,taskname:String(i),drive_type:'quark'}))},()=>version,fn=>{listener=fn},(rows,order,page,size)=>rows.slice((page-1)*size,page*size).map(row=>row.id),
  )
  return {cache,calls,overviewResolve,change(){version++;listener()}}
}
test('default page only reads page; cache keys include filters/page/sort and mutation invalidates', async()=>{
 const {cache,calls,change}=makeCache()
 const query={page:1,page_size:20,task_type:'drama'}
 await cache.loadTaskPage(query); await cache.loadTaskPage(query)
 assert.equal(calls.length,1); assert.equal(calls[0].page,1)
 await cache.loadTaskPage({...query,page:2})
 await cache.loadTaskPage({...query,q:'new'})
 assert.equal(calls.length,3)
 change(); await cache.loadTaskPage(query)
 assert.equal(calls.length,4)
 assert(!calls.includes('overview'))
})
test('name sort reads global filtered index and sends only page-size IDs',async()=>{
 const {cache,calls}=makeCache()
 await cache.loadTaskPage({page:2,page_size:20,name_order:'ascending',q:'same',task_type:'drama'})
 assert.deepEqual(calls[0],{index:{q:'same',task_type:'drama'}})
 assert.equal(calls[1].ids.split(',').length,20)
 assert.equal(calls[1].ids.split(',')[0],'21')
 assert(!calls.includes('overview'))
})
test('overview requests coalesce and late response cannot repopulate invalidated cache',async()=>{
 const {cache,calls,change,overviewResolve}=makeCache()
 const one=cache.loadSharedTasks(),two=cache.loadSharedTasks()
 assert.equal(calls.length,1)
 change();overviewResolve([{id:1}]);await one;await two
 assert.deepEqual(cache.sharedTasks.value,[])
 assert.equal(cache.hasSharedTasks(),false)
})

const view=readFileSync(new URL('../src/views/tasks/DramaTaskView.vue',import.meta.url),'utf8')
function extract(start,end){return ts.transpileModule(view.slice(view.indexOf(start),view.indexOf(end,view.indexOf(start))),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText}
test('late task page cannot overwrite newer filters; deleting last row selects valid page',async()=>{
 const pending=[]
 const js=extract('async function loadPage()', '\nwatch(() => [filters.keyword')
 const state={page:{value:3},pageSize:{value:20},tasks:{value:[]},total:{value:0},facets:{value:{}},loading:{value:false},pageError:{value:''}}
 const load=new Function(...Object.keys(state),'loadTaskPage','readFilters','taskSort','let readVersion=0;'+js+';return loadPage')(...Object.values(state),()=>new Promise(resolve=>pending.push(resolve)),()=>({}),{order:''})
 const old=load(),fresh=load()
 pending[1]({items:[{id:2}],total:45,facets:{failed_count:5}});await fresh
 pending[0]({items:[{id:1}],total:45,facets:{failed_count:1}});await old
 assert.deepEqual(state.tasks.value,[{id:2}]); assert.equal(state.facets.value.failed_count,5)
 const removed=load();pending[2]({items:[],total:40,facets:{}});await removed
 assert.equal(state.page.value,2)
})
test('history chunks use byte next offset, retain content on error and permit retry',async()=>{
 const js=extract('async function loadExecutionChunk()', '\nonMounted(() => {')
 const dialog={taskId:1,executionId:2,nextOffset:0,complete:false,loading:false,error:'',content:''}
 const calls=[];let fail=false
 const load=new Function('runLogDialog','fetchTaskExecutionDetail','let logVersion=0;'+js+';return loadExecutionChunk')(dialog,async(a,b,offset)=>{
   calls.push(offset);if(fail)throw Error('fail')
   return {run_log: offset===0?'中文':'尾',log_next_offset:offset===0?6:9,log_complete:offset!==0,tree_summary:'树',log_truncated:true,tree_truncated:true,status:'success'}
 })
 await load();assert.equal(dialog.content,'中文');assert.equal(dialog.nextOffset,6);assert.equal(dialog.complete,false)
 fail=true;await load();assert.equal(dialog.loading,false);assert.equal(dialog.content,'中文');assert.match(dialog.error,/重试/)
 fail=false;await load();assert.deepEqual(calls,[0,6,6]);assert.equal(dialog.content,'中文尾');assert.equal(dialog.complete,true);assert.equal(dialog.truncated,true)
})
