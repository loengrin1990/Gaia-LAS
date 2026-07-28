/* Small UI-state controller; kept dependency-free for browser and Node checks. */
class ContextCompileController {
  constructor({fetchImpl, workspace, render, message, delay}) {
    this.fetch = fetchImpl;
    this.workspace = workspace;
    this.render = render;
    this.message = message;
    this.delay = delay || (() => Promise.resolve());
    this.generation = 0;
    this.jobId = '';
  }
  active(generation, project) { return generation === this.generation && project === this.workspace(); }
  async start(artifactId) {
    if (this.jobId) return false;
    const generation = ++this.generation; const project = this.workspace();
    const response = await this.fetch(`/api/context/${encodeURIComponent(artifactId)}/compile`, {method:'POST', body:JSON.stringify({project})});
    const data = await response.json();
    if (!response.ok || !this.active(generation, project)) return false;
    this.jobId = data.job_id; await this.poll(data.status_url, generation, project); return true;
  }
  async poll(url, generation, project) {
    while (this.jobId && this.active(generation, project)) {
      const response = await this.fetch(url); const job = await response.json();
      if (!this.active(generation, project)) return;
      this.message(job.message || 'Собираем проектный контекст…', job.status === 'failed');
      if (job.status === 'done') { this.jobId=''; this.render(job.result?.candidates || []); return; }
      if (job.status === 'cancelled') { this.jobId=''; this.message('Сборка контекста отменена. Данные не изменены.', true); return; }
      if (job.status === 'failed') { this.jobId=''; this.message(`Не удалось собрать контекст для одного из фрагментов. Данные не изменены. Код: ${job.error_code || 'CONTEXT_INTERNAL_ERROR'}.`, true); return; }
      await this.delay();
    }
  }
  async cancel() { if (!this.jobId) return false; await this.fetch(`/api/jobs/${encodeURIComponent(this.jobId)}/cancel`, {method:'POST'}); return true; }
  changeWorkspace() { this.generation++; this.jobId=''; }
}
if (typeof module !== 'undefined') module.exports = {ContextCompileController};
