/* Small UI-state controller; kept dependency-free for browser and Node checks. */
class ContextCompileController {
  constructor({fetchImpl, workspace, render, message, delay, state}) {
    this.fetch = fetchImpl;
    this.workspace = workspace;
    this.render = render;
    this.message = message;
    this.delay = delay || (() => Promise.resolve());
    this.state = state || (() => {});
    this.generation = 0;
    this.jobId = '';
  }
  active(generation, project) { return generation === this.generation && project === this.workspace(); }
  async start(artifactId) {
    if (this.jobId) return false;
    const generation = ++this.generation; const project = this.workspace();
    let timer; const controller=typeof AbortController==='undefined'?null:new AbortController();
    try {
      timer=setTimeout(()=>controller?.abort(), 15000);
      const response = await this.fetch(`/api/context/${encodeURIComponent(artifactId)}/compile`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project}), signal:controller?.signal});
      let data; try { data=await response.json(); } catch (_) { throw new Error('invalid_response'); }
      if (!response.ok) throw new Error('request_failed');
      if (!data || typeof data.job_id!=='string' || !data.job_id || typeof data.status_url!=='string' || !data.status_url) throw new Error('invalid_job');
      if (!this.active(generation, project)) return false;
      this.jobId = data.job_id; await this.poll(data.status_url, generation, project); return true;
    } catch (_) {
      this.jobId=''; if (this.active(generation, project)) { this.message('Не удалось начать сборку проектного контекста. Повторите попытку.', true); this.state('error'); }
      return false;
    } finally { clearTimeout(timer); }
  }
  async poll(url, generation, project) {
    while (this.jobId && this.active(generation, project)) {
      let response, job; try { response=await this.fetch(url); job=await response.json(); if(!response.ok) throw new Error('poll_failed'); } catch (_) { this.jobId=''; this.message('Не удалось получить статус сборки контекста. Повторите попытку.', true); this.state('error'); return; }
      if (!this.active(generation, project)) return;
      this.message(job.message || 'Собираем проектный контекст…', job.status === 'failed'); this.state(job.status, job);
      if (job.status === 'done') { this.jobId=''; this.render(job.result?.candidates || []); return; }
      if (job.status === 'cancelled') { this.jobId=''; this.message('Сборка контекста отменена. Данные не изменены.', true); return; }
      if (job.status === 'failed') { this.jobId=''; this.message(`Не удалось собрать контекст для одного из фрагментов. Данные не изменены. Код: ${job.error_code || 'CONTEXT_INTERNAL_ERROR'}.`, true); return; }
      await this.delay();
    }
  }
  async cancel() { if (!this.jobId) return false; await this.fetch(`/api/jobs/${encodeURIComponent(this.jobId)}/cancel`, {method:'POST'}); return true; }
  changeWorkspace() { this.generation++; this.jobId=''; }
}
if (typeof window !== 'undefined') window.ContextCompileController = ContextCompileController;
if (typeof module !== 'undefined') module.exports = {ContextCompileController};
