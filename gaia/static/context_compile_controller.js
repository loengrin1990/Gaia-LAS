/* Small UI-state controller; kept dependency-free for browser and Node checks. */
class ContextCompileController {
  constructor({fetchImpl, workspace, render, message, delay, state, onDiagnostic}) {
    this.fetch = fetchImpl;
    this.workspace = workspace;
    this.render = render;
    this.message = message;
    this.delay = delay || (() => Promise.resolve());
    this.state = state || (() => {});
    this.onDiagnostic = onDiagnostic || (() => {});
    this.generation = 0;
    this.jobId = '';
    this.phase = '';
  }
  active(generation, project) { return generation === this.generation && project === this.workspace(); }
  startupDiagnostic(event, startedAt, fields={}) {
    const safeFields = {event, operation:'context_compile', duration_ms:Math.max(0, Math.round(Date.now()-startedAt))};
    if (Number.isInteger(fields.http_status)) safeFields.http_status = fields.http_status;
    if (fields.error_code) safeFields.error_code = fields.error_code;
    try { this.onDiagnostic(event, safeFields); } catch (_) {}
  }
  startupMessage(errorCode) {
    if (errorCode === 'request_invocation_failed') return 'Не удалось запустить запрос сборки контекста. Перезапустите Gaia и повторите действие.';
    if (errorCode === 'request_rejected') return 'Не удалось связаться с локальным сервисом Gaia. Проверьте, что приложение готово к работе.';
    return 'Не удалось начать сборку проектного контекста. Данные не изменены.';
  }
  async start(artifactId) {
    if (this.jobId) return false;
    const generation = ++this.generation; const project = this.workspace(); const startedAt=Date.now();
    let timer; const controller=typeof AbortController==='undefined'?null:new AbortController();
    let response; let errorCode='request_invocation_failed';
    try {
      timer=setTimeout(()=>controller?.abort(), 15000);
      let request;
      try { request=this.fetch(`/api/context/${encodeURIComponent(artifactId)}/compile`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project}), signal:controller?.signal}); }
      catch (_) { throw null; }
      try { response=await request; } catch (_) { errorCode='request_rejected'; throw null; }
      let data; try { data=await response.json(); } catch (_) { errorCode='invalid_response'; throw null; }
      if (!response.ok) { errorCode='http_rejected'; throw null; }
      if (!data || typeof data.job_id!=='string' || !data.job_id || typeof data.status_url!=='string' || !data.status_url) { errorCode='invalid_job_contract'; throw null; }
      this.startupDiagnostic('request_succeeded', startedAt, {http_status:response.status});
      if (!this.active(generation, project)) return false;
      this.jobId = data.job_id; this.phase=''; await this.poll(data.status_url, generation, project); return true;
    } catch (_) {
      this.startupDiagnostic('request_failed', startedAt, {error_code:errorCode, http_status:response?.status});
      this.jobId=''; this.phase=''; if (this.active(generation, project)) { this.message(this.startupMessage(errorCode), true); this.state('error'); }
      return false;
    } finally { clearTimeout(timer); }
  }
  async poll(url, generation, project) {
    while (this.jobId && this.active(generation, project)) {
      let response, job; try { response=await this.fetch(url); job=await response.json(); if(!response.ok) throw new Error('poll_failed'); } catch (_) { this.jobId=''; this.message('Не удалось получить статус сборки контекста. Повторите попытку.', true); this.state('error'); return; }
      if (!this.active(generation, project)) return;
      this.phase=job.phase || ''; this.message(contextCompileUserMessage(job), ['failed','interrupted','cancelled'].includes(job.status)); this.state(job.status, job);
      if (job.status === 'done') { this.jobId=''; this.phase=''; const candidates=job.result?.candidates || []; this.render(candidates); if (!candidates.length) this.message('В материале не найдено элементов проектного контекста. Данные не изменены.', false); return; }
      if (job.status === 'cancelled') { this.jobId=''; this.phase=''; return; }
      if (job.status === 'failed') { this.jobId=''; this.phase=''; return; }
      if (job.status === 'interrupted') { this.jobId=''; this.phase=''; return; }
      if (job.status === 'complete_empty') { this.jobId=''; this.phase=''; this.render([]); return; }
      await this.delay();
    }
  }
  async resume(jobId, statusUrl, job) {
    if (this.jobId || !jobId || !statusUrl || !isContextCompileActive(job)) return false;
    const generation = this.generation; const project = this.workspace();
    this.jobId = jobId; this.phase = job.phase || ''; this.state(job.status, job);
    await this.poll(statusUrl, generation, project); return true;
  }
  async cancel() { if (!this.jobId) return false; if (this.phase === 'persisting' || this.phase === 'finalizing') { this.message('Сохранение контекста уже завершается. Дождитесь результата.', false); return false; } await this.fetch(`/api/jobs/${encodeURIComponent(this.jobId)}/cancel`, {method:'POST'}); return true; }
  changeWorkspace() { this.generation++; this.jobId=''; this.phase=''; }
}
function isContextCompileActive(job) { return ['created','running'].includes(job?.status); }
function contextCompileUserMessage(job) {
  const status=job?.status || 'not_started';
  if (status === 'done') return 'Контекст собран. Проверьте кандидатов.';
  if (status === 'complete_empty') return 'Сборка завершена успешно, но элементов проектного контекста не найдено.';
  if (status === 'failed') return 'Не удалось завершить сборку проектного контекста. Контекст не изменён.';
  if (status === 'interrupted') return 'Сборка была прервана. Контекст не изменён.';
  if (status === 'cancelled') return 'Сборка контекста отменена. Данные не изменены.';
  return job?.user_message || job?.message || 'Сборка проектного контекста выполняется.';
}
function reviewProgress(candidates) {
  const current=(candidates || []).filter(item=>item.current!==false);
  const pending=current.filter(item=>!['confirmed','rejected','superseded'].includes(item.status));
  return {total:current.length, pending:pending.length, processed:current.length-pending.length};
}
function nextPendingCandidate(candidates) { return (candidates || []).find(item=>item.current!==false && !['confirmed','rejected','superseded'].includes(item.status)) || null; }
function contextCompilePresentation(job, now=Date.now()) {
  const phase={compiling:'Анализ материала',loading_model:'Загрузка локальной модели',validating:'Проверка результата',persisting:'Сохранение контекста',finalizing:'Завершение',interrupted:'Сборка прервана'}[job.phase] || 'Подготовка материала';
  const elapsed=job.started_at ? Math.max(0,Math.floor((now-Date.parse(job.started_at))/1000)) : 0;
  const label=job.status==='failed'?'Повторить сборку':(['cancelled','interrupted'].includes(job.status)?'Запустить заново':'Собрать проектный контекст');
  return {phase,progress:job.total_chunks?`Фрагмент ${job.current_chunk||job.completed_chunks||0} из ${job.total_chunks}`:'',elapsed:`Выполняется: ${elapsed} с`,activity:job.last_activity_at?'Последняя активность: только что':'',restartWarning:isContextCompileActive(job)?'Сборка выполняется. Перезапуск Gaia прервёт текущую попытку.':'',buttonLabel:label,message:contextCompileUserMessage(job)};
}
if (typeof window !== 'undefined') { window.ContextCompileController = ContextCompileController; window.contextCompileUserMessage = contextCompileUserMessage; window.contextCompileIsActive = isContextCompileActive; window.contextReviewProgress = reviewProgress; window.contextNextPending = nextPendingCandidate; }
if (typeof module !== 'undefined') module.exports = {ContextCompileController, contextCompilePresentation, contextCompileUserMessage, isContextCompileActive, reviewProgress, nextPendingCandidate};
