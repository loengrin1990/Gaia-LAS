/* Shared browser/Node local session recovery; never exposes the HttpOnly token. */
(function(root){
  async function safeJson(response){ try { return await response.clone().json(); } catch (_) { return null; } }
  class SessionRecovery {
    constructor(fetchImpl){ this.nativeFetch=fetchImpl; this.runtimeId=''; this.bootstrapPromise=null; }
    async refresh(){ const r=await this.nativeFetch('/api/session/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Gaia-Session-Refresh':'1'},body:'{}'}); const d=await safeJson(r); if(!r.ok||!d||d.status!=='ready'||typeof d.runtime_id!=='string') throw new Error('session_refresh_failed'); this.runtimeId=d.runtime_id; return d; }
    bootstrap(){ if(!this.bootstrapPromise) this.bootstrapPromise=this.refresh(); return this.bootstrapPromise; }
    async mutationFetch(input,init={}){ const method=String(init.method||(input&&input.method)||'GET').toUpperCase(); if(!['POST','PATCH','PUT','DELETE'].includes(method)) return this.nativeFetch(input,init); try { await this.bootstrap(); } catch (_) { /* a later stale-cookie retry may still recover */ }
      if(init.signal?.aborted) throw new DOMException('Aborted','AbortError'); const first=await this.nativeFetch(input,init); const data=await safeJson(first); if(!(first.status===403&&data?.error?.code==='mutation_not_authorized')) return first;
      if(init.signal?.aborted) throw new DOMException('Aborted','AbortError'); await this.refresh(); if(init.signal?.aborted) throw new DOMException('Aborted','AbortError'); return this.nativeFetch(input,init); }
  }
  root.SessionRecovery=SessionRecovery; root.safeJson=safeJson;
  if(typeof module!=='undefined') module.exports={SessionRecovery,safeJson};
})(typeof window!=='undefined'?window:globalThis);
