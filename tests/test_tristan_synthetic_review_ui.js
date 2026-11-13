const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
  constructor(tag=''){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.checked=false;this.hidden=false;this.className='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const descendants=node=>[node,...node.children.flatMap(descendants)];
async function fixture(options={}) {
  const nodes={};const get=id=>nodes[id]||(nodes[id]=new Element());
  let state={phase:'proposed',version:1,mode:'recorded_replay',error:null,task:'Проверить готовность.',view_digest:'v1',
    before:'Статус ожидается.\nОбязанность сохраняется.',memory:'Статус ожидается.\nОбязанность сохраняется.',
    sources:[{name:'Источник.txt',text:'Статус подтверждён. Условие остаётся прежним.'}],accepted_indices:[],operations:[
      {action:'replace',old_text:'Статус ожидается.',new_text:'Статус подтверждён.',quote:'Статус подтверждён.'},
      {action:'append',old_text:'',new_text:'Условие остаётся прежним.',quote:'Условие остаётся прежним.'}]};
  Object.assign(state,options.state||{});let stateReads=0;const calls=[];
  const fetch=async(url,init)=>{
    const action=url.split('/').pop();const body=init?.body?JSON.parse(init.body):null;calls.push({action,body});
    let result;
    if(action==='state'){stateReads++;result=options.stateRequest&&stateReads>1?await options.stateRequest(JSON.parse(JSON.stringify(state))):state;}
    else if(action==='preview')result=options.preview?await options.preview(body):{preview_token:'ticket',after:'Статус подтверждён.\nОбязанность сохраняется.',before:state.before,indices:body.indices,selected_count:body.indices.length};
    else if(action==='confirm'){state={...state,phase:'confirmed',version:2,accepted_indices:body.indices,memory:'Статус подтверждён.\nОбязанность сохраняется.',view_digest:'v2'};result=state;}
    else if(action==='cancel'){state={...state,phase:'cancelled',view_digest:'closed'};result=state;}
    else throw Error('Unexpected route');
    return {ok:!result.errorResponse,json:async()=>result.errorResponse?{error:result.errorResponse}:JSON.parse(JSON.stringify(result))};
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../scripts/tristan_synthetic_review.js'),'utf8'),{
    document:{getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element(),{textContent:text})},fetch});
  await tick();
  const cards=()=>descendants(get('cards'));
  return {nodes,calls,cards,box:i=>cards().filter(n=>n.tag==='input')[i],all:()=>Object.values(nodes).flatMap(descendants),
    select(i,value=true){const box=this.box(i);box.checked=value;box.onchange();}};
}

test('all operations start unchecked; full sources and literal quote are visible; no automatic confirm',async()=>{
 const f=await fixture();assert.equal(f.box(0).checked,false);assert.equal(f.nodes.confirm.disabled,true);
 assert.ok(f.all().some(n=>n.textContent==='Статус подтверждён. Условие остаётся прежним.'));
 assert.ok(f.cards().some(n=>n.tag==='blockquote'&&n.textContent==='Статус подтверждён.'));
 assert.equal(f.calls.filter(c=>c.action!=='state').length,0);
});
test('separate preview plus review checkbox is required before confirm',async()=>{
 const f=await fixture();f.select(0);await f.nodes.preview.onclick();assert.equal(f.nodes.confirm.disabled,true);
 f.nodes.ack.checked=true;f.nodes.ack.onchange();assert.equal(f.nodes.confirm.disabled,false);
 await f.nodes.confirm.onclick();const payload=f.calls.find(c=>c.action==='confirm').body;
 assert.equal(payload.preview_token,'ticket');assert.deepEqual(payload.indices,[0]);assert.equal(payload.confirm,true);
 assert.equal(f.nodes.confirm.disabled,true);assert.match(f.nodes.status.textContent,/Сохранено 1 из 2/);
});
test('changed selection clears preview and acknowledgement; late result cannot enable confirm',async()=>{
 let resolve;const f=await fixture({preview:body=>new Promise(done=>{resolve=()=>done({preview_token:'old',after:'old result',indices:body.indices,selected_count:1});})});
 f.select(0);const pending=f.nodes.preview.onclick();f.select(1);resolve();await pending;
 assert.equal(f.nodes.previewPanel.hidden,true);assert.equal(f.nodes.confirm.disabled,true);assert.equal(f.nodes.ack.checked,false);
 await f.nodes.confirm.onclick();assert.equal(f.calls.filter(c=>c.action==='confirm').length,0);
});
test('excluding one operation changes only local selection and requires fresh preview',async()=>{
 const f=await fixture();f.select(0);await f.nodes.preview.onclick();
 f.cards().find(n=>n.tag==='button'&&n.textContent==='Исключить').onclick();
 assert.equal(f.box(0).checked,false);assert.equal(f.nodes.previewPanel.hidden,true);assert.equal(f.nodes.preview.disabled,true);
 assert.equal(f.calls.filter(c=>c.action==='confirm'||c.action==='cancel').length,0);
});
test('cancel closes without confirm; duplicate UI confirm is disabled',async()=>{
 const f=await fixture();f.select(0);await f.nodes.preview.onclick();await f.nodes.cancel.onclick();
 assert.equal(f.nodes.confirm.disabled,true);assert.match(f.nodes.status.textContent,/без принятия/);
 await f.nodes.confirm.onclick();assert.equal(f.calls.filter(c=>c.action==='confirm').length,0);
});
test('late state refresh must not reopen a cancelled request',async()=>{
 let resolve;const f=await fixture({stateRequest:snapshot=>new Promise(done=>{resolve=()=>done(snapshot);})});
 const pending=f.nodes.refresh.onclick();await f.nodes.cancel.onclick();resolve();await pending;
 assert.match(f.nodes.status.textContent,/без принятия/);assert.equal(f.nodes.cancel.disabled,true);
});
test('empty, incomplete and known blocked conflict explain no candidate without claiming completeness',async()=>{
 for(const [phase,error]of[['proposed',null],['prepared','incomplete_response'],['blocked','unresolved_fixture_conflict']]){
  const f=await fixture({state:{phase,error,operations:[]}});assert.equal(f.nodes.preview.disabled,true);assert.equal(f.nodes.confirm.disabled,true);
  if(phase==='proposed')assert.match(f.nodes.status.textContent,/не доказательство полноты/);
  if(error)assert.equal(f.nodes.error.hidden,false);
  if(phase==='blocked')assert.match(f.nodes.error.textContent,/Общей автоматической проверки.*нет/);
 }
});
test('stale preview error is readable and cannot be accepted',async()=>{
 const f=await fixture({preview:async()=>({errorResponse:'Предпросмотр устарел. Просмотрите текущий выбор заново.'})});
 f.select(0);await f.nodes.preview.onclick();assert.match(f.nodes.error.textContent,/устарел/);assert.equal(f.nodes.confirm.disabled,true);
});
test('response text is rendered as text, never HTML',async()=>{
 const f=await fixture({state:{operations:[{action:'append',old_text:'',new_text:'<img src=x onerror=alert(1)>',quote:'Статус подтверждён.'}]}});
 assert.ok(f.cards().some(n=>n.textContent==='<img src=x onerror=alert(1)>'));
 assert.equal(f.all().some(n=>n.tag==='img'),false);
 assert.equal(/\.innerHTML\s*=/.test(fs.readFileSync(require.resolve('../scripts/tristan_synthetic_review.js'),'utf8')),false);
});
