const assert = require('assert');
const {ContextCompileController} = require('../gaia/static/context_compile_controller.js');
const response = (body, ok=true) => ({ok, json: async()=>body});
(async()=>{
  let workspace='A', rendered=[], messages=[], calls=[];
  const queue=[response({job_id:'old',status_url:'/old'}), response({status:'running',message:'Собираем контекст: фрагмент 1 из 2…'}), response({job_id:'new',status_url:'/new'}), response({status:'done',message:'Контекст собран. Проверьте кандидатов.',result:{candidates:[{title:'B'}]}})];
  const controller=new ContextCompileController({workspace:()=>workspace,render:x=>rendered=x,message:(x,e)=>messages.push([x,e]),delay:async()=>{workspace='B';controller.changeWorkspace();},fetchImpl:async(url)=>{calls.push(url);return queue.shift();}});
  await controller.start('sanA'); assert.deepStrictEqual(rendered,[]); assert.strictEqual(controller.jobId,'');
  await controller.start('sanB'); assert.deepStrictEqual(rendered,[{title:'B'}]); assert.ok(!JSON.stringify(messages).includes('old'));
  assert.ok(calls.some(x=>x==='/new'));
  let cancelCall=''; const cancelling=new ContextCompileController({workspace:()=>workspace,render:()=>{},message:()=>{},fetchImpl:async url=>{cancelCall=url;return response({});}}); cancelling.jobId='job-safe'; assert.strictEqual(await cancelling.cancel(),true); assert.strictEqual(cancelCall,'/api/jobs/job-safe/cancel');
  let lateCancelCall=''; let lateMessage=''; const finalizing=new ContextCompileController({workspace:()=>workspace,render:()=>{},message:x=>lateMessage=x,fetchImpl:async url=>{lateCancelCall=url;return response({});}}); finalizing.jobId='job-finalizing'; finalizing.phase='finalizing'; assert.strictEqual(await finalizing.cancel(),false); assert.strictEqual(lateCancelCall,''); assert.strictEqual(lateMessage,'Сохранение контекста уже завершается. Дождитесь результата.');
  const failing=new ContextCompileController({workspace:()=>workspace,render:()=>{},message:()=>{},fetchImpl:async()=>response({error:'safe'},false)}); assert.strictEqual(await failing.start('san-error'),false); assert.strictEqual(failing.jobId,'');
  const rejected=new ContextCompileController({workspace:()=>workspace,render:()=>{},message:()=>{},fetchImpl:async()=>{throw new Error('network')}}); assert.strictEqual(await rejected.start('san-error'),false); assert.strictEqual(rejected.jobId,'');
  const invalid=new ContextCompileController({workspace:()=>workspace,render:()=>{},message:()=>{},fetchImpl:async()=>({ok:true,json:async()=>({})})}); assert.strictEqual(await invalid.start('san-error'),false); assert.strictEqual(invalid.jobId,'');
  console.log('context compile controller checks passed');
})().catch(error=>{console.error(error);process.exit(1);});
