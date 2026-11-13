import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import {computed, effectScope, nextTick, reactive, ref, watch} from 'vue'

function picker(fetchTMDBImages) {
  const source=readFileSync(new URL('../src/components/tasks/TMDBImagePicker.vue',import.meta.url),'utf8')
  const script=source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'')
  const js=ts.transpileModule(script,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
  const props=reactive({modelValue:false,mediaType:'tv',tmdbId:12,selectedPath:'/original.jpg'})
  const events=[]
  const scope=effectScope()
  const state=scope.run(()=>new Function('defineProps','defineEmits','ref','reactive','computed','watch','fetchTMDBImages',
    js+';return {gallery,activeType,imagePage,visibleImages,loadImages,chooseImage}')(
    ()=>props,(..._)=> (...args)=>events.push(args),ref,reactive,computed,watch,fetchTMDBImages))
  return {props,events,state,stop:()=>scope.stop()}
}

test('image gallery loads only when opened, reuses successful results, and pages thumbnails',async()=>{
  const calls=[]
  const images={posters:Array.from({length:30},(_,i)=>({file_path:`/poster${i}.jpg`,width:600,height:900,language:'zh'})),backdrops:[]}
  const view=picker(async(...args)=>{calls.push(args);return images})
  try {
    assert.equal(calls.length,0)
    view.props.modelValue=true;await nextTick();await nextTick()
    assert.deepEqual(calls,[['tv',12]])
    assert.equal(view.state.visibleImages.value.length,24)
    view.state.imagePage.value=2;assert.equal(view.state.visibleImages.value.length,6)
    view.props.modelValue=false;await nextTick();view.props.modelValue=true;await nextTick()
    assert.equal(calls.length,1)
    view.state.chooseImage(images.posters[0])
    assert.deepEqual(view.events,[['select',images.posters[0],'posters'],['update:modelValue',false]])
  } finally {view.stop()}
})

test('gallery errors remain retryable and a valid empty response is separate',async()=>{
  let fail=true
  const view=picker(async()=>{if(fail)throw Error('offline');return {posters:[],backdrops:[]}})
  try {
    await view.state.loadImages()
    assert.match(view.state.gallery.error,/重试/)
    assert.equal(view.state.gallery.loading,false)
    assert.equal(view.events.length,0)
    fail=false;await view.state.loadImages()
    assert.equal(view.state.gallery.error,'')
    assert.equal(view.state.visibleImages.value.length,0)
  } finally {view.stop()}
})

test('an old gallery response cannot replace another media selection',async()=>{
  const pending=[]
  const view=picker(()=>new Promise(resolve=>pending.push(resolve)))
  try {
    const old=view.state.loadImages()
    view.props.tmdbId=13
    const fresh=view.state.loadImages()
    pending[1]({posters:[{file_path:'/new.jpg'}],backdrops:[]});await fresh
    pending[0]({posters:[{file_path:'/old.jpg'}],backdrops:[]});await old
    assert.equal(view.state.visibleImages.value[0].file_path,'/new.jpg')
  } finally {view.stop()}
})

test('changing the selected image invalidates prepared blobs and late downloads cannot restore the old image',async()=>{
  const source=readFileSync(new URL('../src/views/tasks/CopywritingView.vue',import.meta.url),'utf8')
  const action=source.slice(source.indexOf('async function preparePoster()'),source.indexOf('\nwatch(',source.indexOf('async function preparePoster()')))
  const js=ts.transpileModule(action,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
  const pending=[]
  const url={value:'/original.jpg'},raw={value:'old blob'},png={value:'old png'},preparing={value:false}
  const prepare=new Function('fullPosterUrl','posterRawBlob','posterPngBlob','posterPreparing','fetchPosterBlob','blobToPng',
    'let posterPrepareVersion=0;'+js+';return preparePoster')(url,raw,png,preparing,
    address=>new Promise(resolve=>pending.push({address,resolve})),async blob=>blob)
  const old=prepare()
  url.value='/selected.jpg';const fresh=prepare()
  assert.equal(raw.value,null);assert.equal(png.value,null)
  const selected={type:'image/png',id:'selected'}
  pending[1].resolve({blob:selected});await fresh
  pending[0].resolve({blob:{type:'image/png',id:'original'}});await old
  assert.equal(raw.value,selected);assert.equal(png.value,selected)
  assert.equal(pending[1].address,'/selected.jpg')
  assert.equal(preparing.value,false)
})

test('copy waits for the newly selected image even while its preparation is in progress',async()=>{
  const source=readFileSync(new URL('../src/views/tasks/CopywritingView.vue',import.meta.url),'utf8')
  const action=source.slice(source.indexOf('async function copyPoster()'),source.indexOf('\nasync function downloadPoster()'))
  const js=ts.transpileModule(action,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
  const png={value:null},errors=[]
  const run=new Function('posterPngBlob','posterRawBlob','posterPreparing','preparePoster','canWriteImage','ElMessage','posterDialogVisible',
    js+';return copyPoster')(png,{value:null},{value:true},async()=>{png.value='selected image'},()=>false,{error:message=>errors.push(message)},{value:false})
  await run()
  assert.equal(png.value,'selected image')
  assert.deepEqual(errors,[])
})

test('a default poster request started earlier cannot overwrite a later image choice',async()=>{
  const source=readFileSync(new URL('../src/views/tasks/CopywritingView.vue',import.meta.url),'utf8')
  const select=source.slice(source.indexOf('function selectPoster('),source.indexOf('\nconst TMDB_POSTER_BASE'))
  const load=source.slice(source.indexOf('async function loadPosterFromTMDB()'),source.indexOf('\nasync function copyText()'))
  const js=ts.transpileModule(select+load,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
  const draft={tmdbId:12,mediaType:'tv',posterPath:'/original.jpg',category:'国剧'}
  let resolveDetail
  const {selectPoster,loadPosterFromTMDB}=new Function('draft','selectedImageKind','posterError','posterLoading','fetchTMDBDetail',
    'let posterSelectionVersion=0;'+js+';return {selectPoster,loadPosterFromTMDB}')(
    draft,{value:'posters'},{value:false},{value:false},()=>new Promise(resolve=>{resolveDetail=resolve}))
  const loading=loadPosterFromTMDB()
  selectPoster({file_path:'/chosen.jpg'},'posters')
  resolveDetail({data:{poster_path:'/default.jpg'}});await loading
  assert.equal(draft.posterPath,'/chosen.jpg')
})
