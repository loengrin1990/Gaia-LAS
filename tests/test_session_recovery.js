const assert=require('assert'); const {SessionRecovery}=require('../gaia/static/session_recovery.js');
const response=(body,ok=true,status=ok?200:500)=>({ok,status,clone(){return this},json:async()=>body});
(async()=>{
 let calls=[]; let q=[response({status:'ready',runtime_id:'r'}),response({ok:true})]; let r=new SessionRecovery(async(...a)=>{calls.push(a);return q.shift()}); await r.bootstrap(); await r.mutationFetch('/x',{method:'POST',body:JSON.stringify({x:1})}); assert.equal(calls.length,2);
 calls=[]; q=[response({status:'ready',runtime_id:'r'}),response({error:{code:'mutation_not_authorized'}},false,403),response({status:'ready',runtime_id:'r2'}),response({job_id:'j'},true,202)]; r=new SessionRecovery(async(...a)=>{calls.push(a);return q.shift()}); await r.bootstrap(); let out=await r.mutationFetch('/api/context/x/compile',{method:'POST',body:JSON.stringify({project:'p'})}); assert.equal(out.status,202); assert.equal(calls.length,4); assert.equal(calls.filter(x=>x[0]==='/api/session/refresh').length,2);
 calls=[]; q=[response({status:'ready',runtime_id:'r'}),response({error:{code:'other'}},false,403)]; r=new SessionRecovery(async(...a)=>{calls.push(a);return q.shift()}); await r.bootstrap(); await r.mutationFetch('/x',{method:'POST'}); assert.equal(calls.length,2);
 console.log('session recovery checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
