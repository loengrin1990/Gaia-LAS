'use strict';
(()=>{
 const $=id=>document.getElementById(id);
 const node=(tag,text)=>{const value=document.createElement(tag);value.textContent=text;return value;};
 let view=null,busy=false;
 async function api(path,body){const result=await fetch('/api/'+path,body?{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{credentials:'same-origin',cache:'no-store'});const value=await result.json();if(!result.ok)throw Error(value.error);return value;}
 function show(data){view=data.state;$('version').textContent='· версия '+view.version;$('memory').textContent=view.memory;$('task').textContent=view.task;$('error').hidden=true;
  $('status').textContent=view.phase==='confirmed'?'Выбранные изменения сохранены в рабочей памяти Gaia. Остальные не приняты.':view.phase==='cancelled'?'Проход отменён. Рабочая память осталась прежней.':view.phase==='no_changes'?'Ответ не содержит изменений. Новая версия не создавалась.':view.phase==='proposed'?'Предложение ждёт вашей проверки. Память ещё не изменена.':'Новые источники уже приняты локально. Откройте ответ и проверьте основания.';
  $('materials').replaceChildren();for(const item of data.materials){const box=node('article','');box.append(node('h3',item.name),node('pre',item.text));$('materials').append(box);}
  $('history').replaceChildren();for(const item of data.history){const details=node('details','');details.append(node('summary','Версия '+item.version),node('pre',item.text));$('history').append(details);}
  $('origin').textContent=view.origin==='recorded_replay'?'Прежний настоящий ответ Тристана. Нового модельного запроса нет.':'Учебный ответ, не результат нового запроса модели.';
  $('prepare').textContent=view.phase==='proposed'?'Продолжить проверку изменений':view.origin==='recorded_replay'?'Открыть проверку ответа Тристана':'Открыть проверку учебного ответа';
  const open=['ready','prepared','proposed'].includes(view.phase);$('prepare').disabled=!open||busy;$('reviewLink').hidden=view.phase==='ready';
  $('hint').textContent=open?'Подготовка использует только зафиксированные синтетические источники. Повтор кнопки не создаёт второй проход.':'Проход закрыт. История и результат доступны; повторного принятия не будет.';
 }
 async function refresh(){try{show(await api('workspace'));}catch(e){$('error').textContent=e.message||'Пространство недоступно. Память не изменена.';$('error').hidden=false;$('prepare').disabled=true;}}
 $('prepare').onclick=async()=>{if(busy||!view||$('prepare').disabled)return;busy=true;$('prepare').disabled=true;$('refresh').disabled=true;
  try{await api('prepare',{view_digest:view.view_digest});window.location.href='/';}
  catch(e){$('error').textContent=e.message;$('error').hidden=false;}
  finally{busy=false;$('refresh').disabled=false;}
 };
 $('refresh').onclick=()=>{if(!busy)return refresh();};refresh();
})();
