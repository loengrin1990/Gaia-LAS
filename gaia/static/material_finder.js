/* Read-only local discovery using existing workspace-bound APIs. No query telemetry or storage. */
function createMaterialFinder({document, fetch, getProject, onOpen}) {
  const $=id=>document.getElementById(id), results=$('materialFindResults'), info=$('materialFindStatus');
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  let generation=0,abort=null,items=[],offset=0,project='',terms=[],context=[],contextKnown=false,total=0,unavailable=0;
  const normalize=text=>String(text||'').normalize('NFKC').toLocaleLowerCase('ru-RU').replace(/ё/g,'е');
  const current=(g,p)=>g===generation&&p===getProject();
  async function get(url,signal){const response=await fetch(url,{signal,cache:'no-store'});if(!response.ok)throw Error('unavailable');return response.json();}
  function reset(){generation++;abort?.abort();abort=null;items=[];context=[];offset=0;total=0;unavailable=0;results.replaceChildren();info.textContent='';$('materialFindMore').hidden=true;$('materialFindQuery').value='';$('materialFindButton').disabled=false;}
  function matches(text){const value=normalize(text);return terms.every(term=>value.includes(term));}
  function render(material,review,related,g,p){
    const text=review.cleaned_text||'';const accepted=related.filter(x=>x.status==='confirmed'&&x.current!==false);
    const pending=related.filter(x=>['requires_review','conflicted'].includes(x.status)&&x.current!==false);
    if(!matches(text+' '+related.map(x=>x.title+' '+x.statement).join(' ')))return;
    total++;
    const card=make('article');card.className='journey-card';
    const pos=terms.length?normalize(text).indexOf(terms[0]):0;const start=Math.max(0,pos-90);const excerpt=text.slice(start,start+320).trim();
    const lines=text.split('\n').map(s=>s.trim()).filter(s=>s&&!/^[#\s]*[А-ЯЁA-Z\s]+$/.test(s));
    card.append(make('h3',(lines[0]||'Очищенный материал').slice(0,100)),make('p',(start?'…':'')+excerpt+(start+320<text.length?'…':'')));
    const state=make('p');state.className='journey-meta';state.textContent=review.confirmed?'Очищенная версия подтверждена.':'Очищенная версия ещё не подтверждена.';card.append(state);
    const note=make('p');note.textContent=contextKnown?`Принято в проектный контекст: ${accepted.length}. Ожидает проверки: ${pending.length}.`:'Сведения о принятии в проектный контекст сейчас недоступны.';card.append(note);
    if(accepted.length){const box=make('details');box.append(make('summary','Что принято из этого материала'));for(const item of accepted)box.append(make('h4',item.title),make('p',item.statement));card.append(box);}
    if(pending.length)card.append(make('p','Ожидающие проверки предложения не считаются принятой памятью.'));
    const full=make('details');const pre=make('pre',text);pre.className='journey-evidence';full.append(make('summary','Полный очищенный материал'),pre);card.append(full);
    const open=make('button',review.confirmed?'Открыть очищенную версию':'Перейти к проверке');open.type='button';open.onclick=()=>{if(current(g,p))onOpen(material.sanitized_id);};card.append(open);results.append(card);
  }
  async function scan(g,p){
    $('materialFindButton').disabled=true;$('materialFindMore').disabled=true;const end=Math.min(offset+50,items.length);
    try {
      while(offset<end){if(!current(g,p))return;const item=items[offset++];
        try {const view=await get(`/api/reviews/${encodeURIComponent(item.sanitized_id)}?project=${encodeURIComponent(p)}&read_only=1`,abort.signal);
          if(!current(g,p))return;if(view.artifact_id!==item.sanitized_id||typeof view.cleaned_text!=='string'){unavailable++;continue;}
          render(item,view,context.filter(x=>(x.source_links||[]).includes(item.sanitized_id)&&x.current!==false&&!['rejected','superseded'].includes(x.status)),g,p);
        }catch(e){if(!current(g,p))return;unavailable++;}
        info.textContent=`Проверено материалов: ${offset} из ${items.length}. Найдено: ${total}.`;
      }
      if(current(g,p)){info.textContent=`Проверено материалов: ${offset} из ${items.length}. Найдено: ${total}.`+(unavailable?` Недоступны для чтения: ${unavailable}.`:'')+(offset<items.length?' Поиск ещё не завершён.':' Поиск завершён.')+' Принятие в контекст показано на момент поиска; оно не равно подтверждению очищенного текста.';$('materialFindMore').hidden=offset>=items.length;}
    } finally {if(current(g,p)){$('materialFindButton').disabled=false;$('materialFindMore').disabled=false;}}
  }
  async function search(){
    generation++;abort?.abort();abort=new AbortController();const g=generation,p=getProject();project=p;results.replaceChildren();offset=0;total=0;unavailable=0;items=[];context=[];contextKnown=false;$('materialFindMore').hidden=true;
    const query=$('materialFindQuery').value.trim();terms=normalize(query).split(/[^\p{L}\p{N}_-]+/u).filter(Boolean);
    if(!p){info.textContent='Выберите рабочее пространство.';return;}
    if(query.length>200||terms.length>16){info.textContent='Укажите запрос до 200 символов и 16 слов.';return;}
    info.textContent='Ищем локально в очищенных материалах…';$('materialFindButton').disabled=true;
    try {
      const data=await get(`/api/materials?project=${encodeURIComponent(p)}`,abort.signal);if(!current(g,p))return;
      items=(data.materials||[]).filter(x=>x.current!==false&&x.sanitized_id);
      try{const data=await get(`/api/context?project=${encodeURIComponent(p)}`,abort.signal);if(!current(g,p))return;context=data.candidates||[];contextKnown=true;}catch(e){if(!current(g,p))return;}
      await scan(g,p);
    }catch(e){if(current(g,p)){info.textContent='Поиск не завершён. Данные не изменены. Повторите попытку.';$('materialFindButton').disabled=false;}}
  }
  $('materialFindForm').onsubmit=e=>{e.preventDefault();return search();};
  $('materialFindClear').onclick=reset;
  $('materialFindMore').onclick=()=>{if(!$('materialFindMore').disabled)return scan(generation,project);};
  return {reset,search};
}
