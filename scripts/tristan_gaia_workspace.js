'use strict';
(()=>{
 const $=id=>document.getElementById(id);
 const node=(tag,text)=>{const value=document.createElement(tag);value.textContent=text;return value;};
 let view=null,busy=false,generation=0;
 async function api(path,body){const result=await fetch('/api/'+path,body?{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{credentials:'same-origin',cache:'no-store'});const value=await result.json();if(!result.ok)throw Error(value.error);return value;}
 function show(data){view=data.state;$('version').textContent='· версия '+view.version;$('memory').textContent=view.memory;$('task').textContent=view.task;$('error').hidden=true;
  $('status').textContent=view.phase==='confirmed'?'Выбранные изменения сохранены в рабочей памяти Gaia. Остальные не приняты.':view.phase==='cancelled'?'Проход отменён. Рабочая память осталась прежней.':view.phase==='no_changes'?'Ответ не содержит изменений. Новая версия не создавалась.':view.phase==='proposed'?'Предложение ждёт вашей проверки. Память ещё не изменена.':'Новые источники уже приняты локально. Откройте ответ и проверьте основания.';
  $('materials').replaceChildren();for(const item of data.materials){const box=node('article','');box.append(node('h3',item.name),node('pre',item.text));$('materials').append(box);}
  $('history').replaceChildren();for(const item of data.history){const details=node('details','');details.append(node('summary','Версия '+item.version),node('pre',item.text));$('history').append(details);}
  $('round').textContent='Проход '+(view.round_number||1)+' · новые основания отдельно от принятой памяти';
  $('origin').textContent=view.origin==='offline_followup'?'Новое учебное письмо и новый offline-ответ на принятую версию 2. Прежний ответ здесь недействителен.':view.origin==='recorded_replay'?'Прежний настоящий ответ Тристана. Нового модельного запроса нет.':'Учебный ответ, не результат нового запроса модели.';
  $('prepare').textContent=view.phase==='proposed'?'Продолжить проверку изменений':view.origin==='recorded_replay'?'Открыть проверку ответа Тристана':'Открыть проверку учебного ответа';
  const open=['ready','prepared','proposed'].includes(view.phase);$('prepare').disabled=!open||busy;$('reviewLink').hidden=view.phase==='ready';
  $('advance').hidden=!view.can_advance;$('advance').disabled=!view.can_advance||busy;
  $('nextHint').textContent=view.can_advance?'Первый проход закрыт. Чтобы обновить принятую версию 2, явно добавьте новое учебное письмо. Старый ответ повторно не применяется.':(view.round_number===2?'Это новый проход на версии 2. Результат первого уже в памяти и истории; старые preview и ответы к нему не относятся.':'');
  $('passes').replaceChildren();for(const pass of data.passes||[]){const labels={accepted:'принят',cancelled:'отменён',addressed_proposed:'ждёт проверки',addressed_prepared:'подготовлен',addressed_no_changes:'без изменений',ready:'новое письмо'};
   $('passes').append(node('p','Проход '+pass.number+' · основа: версия '+pass.base_version+' · '+(labels[pass.status]||'недоступен')+' · принято операций: '+pass.accepted_count));}
  $('hint').textContent=open?'Подготовка использует только зафиксированные синтетические источники. Повтор кнопки не создаёт второй проход.':'Проход закрыт. История и результат доступны; повторного принятия не будет.';
 }
 async function refresh(){const stamp=++generation;try{const data=await api('workspace');if(stamp===generation)show(data);}catch(e){if(stamp!==generation)return;$('error').textContent=e.message||'Пространство недоступно. Память не изменена.';$('error').hidden=false;$('prepare').disabled=true;}}
 $('prepare').onclick=async()=>{if(busy||!view||$('prepare').disabled)return;generation++;busy=true;$('prepare').disabled=true;$('refresh').disabled=true;
  try{await api('prepare',{view_digest:view.view_digest});window.location.href='/';}
  catch(e){$('error').textContent=e.message;$('error').hidden=false;}
  finally{busy=false;$('refresh').disabled=false;}
 };
 $('advance').onclick=async()=>{if(busy||!view?.can_advance||$('advance').disabled)return;generation++;busy=true;$('advance').disabled=true;$('prepare').disabled=true;$('refresh').disabled=true;
  try{await api('advance',{view_digest:view.view_digest});window.location.href='/workspace';}
  catch(e){$('error').textContent=e.message;$('error').hidden=false;}finally{busy=false;$('refresh').disabled=false;}
 };
 $('refresh').onclick=()=>{if(!busy)return refresh();};refresh();
})();
