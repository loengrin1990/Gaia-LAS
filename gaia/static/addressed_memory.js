(() => {
  let requestId = null, busy = false;
  const progress = text => { $('addressedProgress').textContent = text; };
  const call = (action, payload = {}) => api('addressed/' + action, payload);
  function hideLegacyActions() {
    $('prepareMemory').parentElement.style.display = 'none';
    $('chatExport').hidden = true;
    $('memoryInputs').hidden = true;
    $('memoryReply').closest('details').hidden = true;
    const notice = document.querySelector('.notice');
    if (notice) notice.textContent = 'Адресный пилот работает только на этом компьютере. Проверьте основания и смысл выбранных изменений перед подтверждением. Остальной текст памяти сохраняется буквально; автоматической внешней передачи нет.';
  }
  function block(value) {
    const pre = document.createElement('div'); pre.className = 'pre'; pre.textContent = value; return pre;
  }
  async function reload() {
    const data = await call('overview');
    await refreshMemory(); hideLegacyActions();
    requestId = data.prepared.at(-1)?.id || null;
    $('addressedAnalyze').disabled = !requestId || busy;
    $('addressedCancel').disabled = !requestId || busy;
    if (requestId && !busy) {
      const view = await call('inputs', {id: requestId});
      $('addressedInputs').textContent = 'ЗАДАЧА\n' + (view.task || '') + '\n\nПРЕЖНЯЯ ПАМЯТЬ\n' + view.before + '\n\nИСТОЧНИКИ\n' + view.evidence.join('\n\n');
    }
    $('addressedProposals').replaceChildren();
    for (const proposal of data.proposals) {
      const box = document.createElement('div'); box.className = 'split';
      const heading = document.createElement('h3');
      heading.textContent = proposal.stale ? 'Предложение устарело' : 'Выберите изменения для проверки';
      box.append(heading);
      if (proposal.task) box.append(block('Задача этого прохода\n' + proposal.task));
      const sources = document.createElement('details'), sourceTitle = document.createElement('summary');
      sourceTitle.textContent = 'Полные основания этого прохода';
      sources.append(sourceTitle, block((proposal.evidence || []).map((text, i) => 'Источник ' + (i + 1) + '\n' + text).join('\n\n')));
      box.append(sources);
      let preview = null, reviewEpoch = 0;
      const checks = [], editors = [];
      const display = document.createElement('div');
      const review = document.createElement('button'); review.textContent = 'Просмотреть выбранные изменения';
      const accept = document.createElement('button'); accept.textContent = 'Подтвердить показанные изменения'; accept.disabled = true;
      const cancel = document.createElement('button'); cancel.textContent = 'Отменить предложение';
      for (const change of proposal.changes) {
        const label = document.createElement('label'), input = document.createElement('input'); input.type = 'checkbox';
        input.disabled = proposal.stale;
        input.onchange = () => { reviewEpoch += 1; preview = null; accept.disabled = true; display.replaceChildren(); };
        checks.push(input);
        label.append(input, document.createTextNode(' Включить: ' + change.action));
        const origins = (proposal.evidence || []).flatMap((text, i) => text.includes(change.quote) ? [i + 1] : []);
        box.append(label, block('Было\n' + (change.before || 'Добавление в конец') + '\n\nПредлагается\n' + change.after + '\n\nОснование — дословная цитата, источник ' + origins.join(', ') + '\n' + change.quote));
        for (const origin of origins) {
          const source = proposal.evidence[origin - 1], start = source.indexOf(change.quote);
          const left = Math.max(0, start - 240), right = Math.min(source.length, start + change.quote.length + 480);
          box.append(block('Источник ' + origin + ' — контекст цитаты (фрагмент; полный текст выше)\n' +
            (left ? '…' : '') + source.slice(left, right) + (right < source.length ? '…' : '')));
        }
        box.append(block('Проверьте: цитата подтверждает именно эту замену, а не только упоминает тему? Не превращены ли предложение, вопрос или неясное «у неё» в утверждённый факт? Не подменена ли постоянная обязанность сообщением о разовом событии? Если основание не подтверждает замену, оставьте её невыбранной; остальные изменения можно принять отдельно.'));
        const editLabel = document.createElement('label'), editor = document.createElement('textarea');
        editLabel.textContent = 'Уточнить текст изменения ' + (editors.length + 1); editor.value = change.after; editor.rows = 3; editor.disabled = proposal.stale;
        editor.oninput = () => { reviewEpoch += 1; preview = null; accept.disabled = true; review.disabled = true; display.replaceChildren(); };
        editors.push(editor); editLabel.append(editor); box.append(editLabel);
      }
      const save = document.createElement('button'); save.textContent = 'Сохранить правку предложения'; save.disabled = proposal.stale;
      save.onclick = async () => {
        try { await call('revise', {id: proposal.id, revision: proposal.revision, texts: editors.map(e => e.value)}); progress('Правка предложения сохранена. Проверьте результат заново; принятая память ещё не изменена.'); await reload(); }
        catch (error) { progress(error.message); }
      };
      review.disabled = proposal.stale;
      review.onclick = async () => {
        const epoch = ++reviewEpoch;
        preview = null; accept.disabled = true;
        try {
          const response = await call('selection', {id: proposal.id, selected: checks.flatMap((c, i) => c.checked ? [i] : [])});
          if (epoch !== reviewEpoch) return;
          preview = response;
          display.replaceChildren(block('Полная память после выбранных изменений\n' + preview.after)); accept.disabled = false;
        } catch (error) { if (epoch === reviewEpoch) progress(error.message); }
      };
      accept.onclick = async () => {
        if (!preview) return;
        accept.disabled = true;
        try {
          await call('decide', {...preview, decision: 'accept'});
          progress('Сохранено изменений: ' + preview.selected.length + ' из ' + proposal.changes.length + '. Остальной текст не переписан.' +
            (preview.selected.length < proposal.changes.length ? ' Невыбранные изменения не приняты. Для их пересмотра подготовьте новый проход.' : ''));
          await reload();
        }
        catch (error) { preview = null; display.replaceChildren(); progress(error.message); }
      };
      cancel.onclick = async () => {
        try { await call('decide', {id: proposal.id, decision: 'cancel'}); progress('Предложение отменено. Принятая память не изменена.'); await reload(); }
        catch (error) { progress(error.message); }
      };
      box.append(save, review, accept, cancel, display); $('addressedProposals').append(box);
    }
  }
  $('addressedPrepare').onclick = async () => {
    if (busy) return;
    try {
      const current = await call('overview');
      const view = await call('prepare', {version: current.version, task: $('task').value.trim() || 'Предложить изменения по новым источникам.', selected: [...selected.values()]});
      requestId = view.id;
      $('addressedInputs').textContent = 'ЗАДАЧА\n' + (view.task || '') + '\n\nПРЕЖНЯЯ ПАМЯТЬ\n' + view.before + '\n\nИСТОЧНИКИ\n' + view.evidence.join('\n\n');
      $('addressedAnalyze').disabled = false; $('addressedCancel').disabled = false;
      progress('Версия памяти и источники зафиксированы. Изменения ещё не приняты.');
    } catch (error) { progress(error.message); }
  };
  $('addressedAnalyze').onclick = async () => {
    if (!requestId || busy) return;
    const id = requestId; busy = true; $('addressedAnalyze').disabled = true; $('addressedPrepare').disabled = true;
    try {
      await call('analyze', {id}); let job;
      do { await new Promise(resolve => setTimeout(resolve, 1000)); job = await call('job', {id}); progress(job.message); } while (job.state === 'running');
      await reload();
    } catch (error) { progress(error.message); }
    finally { busy = false; $('addressedPrepare').disabled = false; await reload(); }
  };
  $('addressedCancel').onclick = async () => {
    if (!requestId) return;
    try { await call('decide', {id: requestId, decision: 'cancel'}); progress('Проход отменён. Принятая память не изменена; расчёт может завершиться в фоне.'); await reload(); }
    catch (error) { progress(error.message); }
  };
  $('addressedRefresh').onclick = () => reload().catch(error => progress(error.message));
  reload().catch(error => progress(error.message));
})();
