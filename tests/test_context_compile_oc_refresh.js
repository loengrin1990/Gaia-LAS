const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const {ContextCompileController,contextCompileUserMessage}=require('../gaia/static/context_compile_controller.js');
const html=fs.readFileSync(require.resolve('../gaia/static/index.html'),'utf8');
const response=(data,ok=true)=>({ok,json:async()=>data});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(fetch){
 let project='A';const nodes={},rendered=[];
 const context={fetch,journeyProject:()=>project,renderOperationalContextReview:data=>rendered.push(data),document:{getElementById:id=>nodes[id]??=( {textContent:'old '+id,replaceChildren(){this.textContent='';}} )}};
 vm.createContext(context);
 const start=html.indexOf('    let operationalReviewGeneration =');
 const end=html.indexOf('    function renderJourneyContext(',start);
 assert.ok(start>=0&&end>start);vm.runInContext(html.slice(start,end),context);
 return {context,nodes,rendered,project:value=>{project=value;},load:()=>context.loadOperationalContextReview()};
}
test('OC-only completion is successful and refreshes both existing review sections',async()=>{
 let render,legacy,refreshes=0;const messages=[];
 const init=html.split('\n').find(line=>line.includes('const journeyContextCompiler = new ContextCompileController('));
 const context={ContextCompileController:class{constructor(options){render=options.render;}},window:{fetch:()=>{}},journeyProject:()=> 'A',renderJourneyContext:x=>legacy=x,loadOperationalContextReview:()=>{refreshes++;},journeyMessage:()=>{}};
 vm.runInNewContext(init,context);
 const controller=new ContextCompileController({fetchImpl:async()=>response({status:'done',result:{candidates:[],operational_context_count:1}}),workspace:()=> 'A',render,message:x=>messages.push(x)});
 controller.jobId='job';await controller.poll('/job',0,'A');
 assert.equal(legacy.length,0);assert.equal(refreshes,1);assert.equal(controller.jobId,'');
 assert.deepEqual(messages,['Сборка завершена. Проверьте предложения контекста проекта.']);
 assert.ok(!messages.some(x=>/не найдено|не предложила/.test(x)));
});
test('late response from another workspace is discarded and old panels cleared',async()=>{
 const a=deferred(),b=deferred();const calls=[];const f=fixture(url=>{calls.push(url);return calls.length===1?a.promise:b.promise;});
 const first=f.load();f.project('B');const second=f.load();
 for(const id of ['operationalContextReview','operationalCurrentContext','operationalDeferredList','operationalHistoryList'])assert.equal(f.nodes[id].textContent,'Загружаем рабочий контекст выбранного проекта…');
 b.resolve(response({project:'B'}));await second;a.resolve(response({project:'A'}));await first;
 assert.equal(f.rendered.length,1);assert.equal(f.rendered[0].project,'B');assert.equal(calls[1],'/api/operational-context/review?project=B');
});
test('same-project refresh supersedes an earlier request and ignores its late error',async()=>{
 const a=deferred(),b=deferred();let calls=0;const f=fixture(()=>++calls===1?a.promise:b.promise);
 const first=f.load(),second=f.load();b.resolve(response({current:'new'}));await second;a.reject(new Error('old transport failure'));await first;
 assert.equal(f.rendered.length,1);assert.equal(f.rendered[0].current,'new');assert.ok(!f.nodes.operationalContextReview.textContent.includes('Не удалось'));
});
test('review HTTP failure is visible without raw diagnostics or fabricated empty count',async()=>{
 const f=fixture(async()=>response({error:'secret_internal_value'},false));await f.load();
 assert.equal(f.rendered.length,0);assert.equal(f.nodes.operationalContextReview.textContent,'Не удалось загрузить рабочий контекст. Повторите открытие раздела.');
 assert.match(contextCompileUserMessage({status:'complete_empty'}),/Модель не предложила.*не гарантирует/);
});
