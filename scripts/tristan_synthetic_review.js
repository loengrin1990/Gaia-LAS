'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const element = (tag, text, cls) => { const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls)node.className=cls; return node; };
  let state=null, selected=new Set(), preview=null, generation=0, loading=false, mutating=false;
  const boxes=[];
  const active=()=>state && ['prepared','proposed'].includes(state.phase);
  const indices=()=>[...selected].sort((a,b)=>a-b);
  async function api(action, body) {
    const response=await fetch('/api/'+action,body===undefined?{credentials:'same-origin',cache:'no-store'}:
      {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();
    if(!response.ok)throw Error(value.error || 'Действие не завершено. Обновите обзор.');
    return value;
  }
  function buttons() {
    $('preview').disabled=!active() || state.phase!=='proposed' || !selected.size || loading || mutating;
    $('confirm').disabled=!active() || !preview || !$('ack').checked || loading || mutating;
    $('cancel').disabled=!active() || mutating;
    $('refresh').disabled=mutating;
    for(const box of boxes)box.disabled=!active() || mutating;
    $('selection').textContent=state.phase==='blocked'?'Кандидат не создавался: этот известный кейс заблокирован заранее.':active() ? `Выбрано ${selected.size} из ${state.operations.length}. Невыбранные изменения не будут приняты.` : 'Проход закрыт. Ниже показано исходное предложение и результат вашего выбора.';
  }
  function invalidate() {
    generation++; preview=null; $('ack').checked=false; $('previewPanel').hidden=true; $('confirm').disabled=true;
  }
  function error(message) { invalidate(); $('error').textContent=message; $('error').hidden=false; buttons(); }
  function sources(quote) {
    $('sources').replaceChildren();let highlight=null;
    state.sources.forEach((source,index)=>{
      const article=element('article'); article.id='source-'+index;
      article.append(element('h3',source.name));
      const text=element('div',undefined,'sourceText');
      const start=quote?source.text.indexOf(quote):-1;
      if(start<0)text.textContent=source.text;
      else { const mark=element('mark',quote);highlight=highlight||mark;
        text.append(document.createTextNode(source.text.slice(0,start)),mark,document.createTextNode(source.text.slice(start+quote.length))); }
      article.append(text);$('sources').append(article);
    });
    highlight?.scrollIntoView?.({block:'center',behavior:'smooth'});
  }
  function render(value) {
    invalidate();state=value;selected=new Set();boxes.length=0;loading=false;
    $('workspaceLink').hidden=!state.workspace;
    $('mode').textContent=state.mode==='gaia_trial' ? (state.origin==='offline_followup'?'Новый учебный ответ на принятую версию 2. Старый ответ первого прохода применять нельзя.':state.origin==='recorded_replay'?'Прежний ответ Тристана. Подтверждение сохранит выбранное в синтетической рабочей памяти Gaia.':'Учебный ответ. Подтверждение сохранит выбранное только в синтетической рабочей памяти Gaia.') : state.mode==='recorded_replay' ? 'Повтор ранее полученного синтетического ответа. Нового запроса модели нет.' : state.mode==='offline_demo' ? 'Offline-демонстрация сценария. Это не ответ модели; новых запросов нет.' : 'Отдельная временная синтетическая сессия. Внешней отправки нет.';
    $('task').textContent='Задача: '+state.task;$('memory').textContent=state.memory;$('version').textContent='· версия '+state.version;
    const closed=state.phase==='confirmed' || state.phase==='cancelled';
    $('memoryNote').textContent=state.phase==='confirmed'?'Показан подтверждённый итог. Проход закрыт.':state.phase==='cancelled'?'Изменения не приняты. Память осталась прежней.':'До подтверждения она остаётся прежней.';
    $('status').textContent=state.phase==='ready'?'Сначала подготовьте просмотр из рабочего пространства Gaia.':state.phase==='confirmed' ? `Сохранено ${state.accepted_indices.length} из ${state.operations.length} изменений. Невыбранные не приняты. Проход закрыт.` : state.phase==='cancelled' ? 'Проход закрыт без принятия изменений. Память осталась прежней.' : state.phase==='blocked' ? 'Кейс заблокирован: изменений нет.' : state.operations.length ? 'Память ещё не изменена. Выберите только проверенные изменения.' : state.phase==='prepared' ? 'Готового кандидата нет. Память не изменена.' : 'Изменений не предложено. Это не доказательство полноты разбора источников.';
    $('error').hidden=!state.error;
    if(state.error)$('error').textContent=state.error==='unresolved_fixture_conflict' ? 'Известный противоречивый кейс заблокирован заранее. Общей автоматической проверки смысловых конфликтов здесь нет.' : 'Ответ не прошёл проверку завершённости или привязки. Кандидат не принят. Новый запрос автоматически не запускается.';
    $('cards').replaceChildren();sources();
    state.operations.forEach((operation,index)=>{
      const card=element('article',undefined,'card'); const label=element('label'); const box=element('input');box.type='checkbox';box.checked=closed && state.accepted_indices.includes(index);boxes.push(box);
      label.append(box,document.createTextNode(closed ? (box.checked?'Принято':'Не принято') : 'Включить в итог'));
      card.append(element('h3',`Изменение ${index+1} · ${operation.action==='append'?'добавление':'замена'}`),label,
        element('p','Было','small'),element('div',operation.old_text || '(новый фрагмент)','old'),
        element('p','Предложено','small'),element('div',operation.new_text,'new'),element('p','Буквальное основание','small'),element('blockquote',operation.quote,'quote'));
      const matches=state.sources.filter(source=>source.text.includes(operation.quote));
      card.append(element('p','Источник: '+matches.map(source=>source.name).join(', '),'small'));
      const evidence=element('button','Показать основание');evidence.onclick=()=>sources(operation.quote);card.append(evidence);
      box.onchange=()=>{invalidate();box.checked?selected.add(index):selected.delete(index);card.className=box.checked?'card selected':'card';buttons();};
      if(!closed){const reject=element('button','Исключить');reject.onclick=()=>{box.checked=false;selected.delete(index);invalidate();card.className='card excluded';buttons();};card.append(reject);}
      $('cards').append(card);
    });
    buttons();
  }
  $('ack').onchange=buttons;
  $('preview').onclick=async()=>{
    if($('preview').disabled)return;
    invalidate();const stamp=generation, chosen=indices(), view=state.view_digest;loading=true;buttons();$('error').hidden=true;
    try{
      const result=await api('preview',{view_digest:view,indices:chosen});
      if(stamp!==generation || !active() || state.view_digest!==view)return;
      preview=result;$('after').textContent=result.after;$('previewCount').textContent=`В итог войдёт ${result.selected_count} из ${state.operations.length} изменений.`;
      $('previewPanel').hidden=false;$('previewPanel').scrollIntoView?.({block:'start',behavior:'smooth'});
    }catch(e){if(stamp===generation)error(e.message);}
    finally{loading=false;buttons();}
  };
  $('confirm').onclick=async()=>{
    if($('confirm').disabled || !preview || !$('ack').checked)return;
    const body={view_digest:state.view_digest,indices:indices(),preview_token:preview.preview_token,confirm:true};
    mutating=true;buttons();
    try{render(await api('confirm',body));}catch(e){error(e.message);}
    finally{mutating=false;buttons();}
  };
  $('cancel').onclick=async()=>{
    if($('cancel').disabled)return;
    invalidate();mutating=true;buttons();
    try{render(await api('cancel',{view_digest:state.view_digest}));}catch(e){error(e.message);}
    finally{mutating=false;buttons();}
  };
  $('refresh').onclick=async()=>{
    invalidate();const stamp=generation;loading=true;buttons();
    try{const value=await api('state');if(stamp===generation)render(value);}
    catch(e){if(stamp===generation){loading=false;error(e.message);}}
    finally{if(stamp===generation){loading=false;buttons();}}
  };
  api('state').then(render).catch(e=>{state={phase:'unavailable',operations:[]};error('Не удалось открыть синтетическую сессию. '+e.message);});
})();
