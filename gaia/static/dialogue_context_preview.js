/* A view of the existing local DialogueContext; never a new package or export format. */
function createDialogueContextPreview({document,fetch,getProject}) {
  const root=document.getElementById('dialogueContextPreview');
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  let generation=0,abort=null;
  function reset(){generation++;abort?.abort();abort=null;root.replaceChildren();root.hidden=true;}
  function render(data){
    reset();const g=generation,p=data.project,context=data.dialogue_context;
    abort=new AbortController();root.hidden=false;
    root.append(make('h3','Принятый контекст в подготовленном пакете'));
    if(!context||!Array.isArray(context.current_authority)){
      root.append(make('p','Этот пакет подготовлен без отбора принятого проектного контекста. Это не означает, что в проекте нет принятых фактов. Используйте «Подготовить контекст без ответа».'));return;
    }
    const items=context.current_authority,metadata=context.metadata||{};
    root.append(make('p',`Включено принятых фактов: ${items.length}. Память Lore — отдельный слой знаний и истории.`));
    if(!items.length)root.append(make('p','В этот пакет не включены принятые факты по вопросу. Уточните предмет вопроса; это не означает, что весь проектный контекст пуст.'));
    if(metadata.selected_context_count>items.length)root.append(make('p','Часть найденного контекста не вошла в лимит пакета. Сузьте вопрос и подготовьте контекст заново.'));
    root.append(make('p','Это снимок на момент подготовки, а не новое подтверждение памяти. Для нового вопроса или после изменения источников подготовьте пакет заново. Внешняя передача этим действием не разрешена.'));
    const current=()=>g===generation&&p===getProject();
    for(const item of items){
      const card=make('article');card.className='journey-card';card.append(make('h4',item.title),make('p',item.statement));
      const sources=Array.isArray(item.source_links)?[...new Set(item.source_links)]:[];
      if(!sources.length)card.append(make('p','Основание не указано. Не считайте факт проверенным по источнику.'));
      sources.forEach((id,index)=>{
        const button=make('button',`Показать очищенное основание ${index+1}`);button.type='button';
        const output=make('div');
        button.onclick=async()=>{
          if(!current()||button.disabled)return;button.disabled=true;output.replaceChildren(make('p','Проверяем доступность основания…'));
          try{
            const response=await fetch(`/api/reviews/${encodeURIComponent(id)}?project=${encodeURIComponent(p)}&read_only=1`,{signal:abort.signal,cache:'no-store'});
            const result=await response.json();if(!current())return;
            if(!response.ok||result.artifact_id!==id||typeof result.cleaned_text!=='string')throw Error('unavailable');
            const text=make('pre',result.cleaned_text);text.className='journey-evidence';
            output.replaceChildren(make('p','Полный очищенный материал. Его остальные утверждения не становятся принятой памятью.'),text);
          }catch(error){if(current())output.replaceChildren(make('p','Не удалось подтвердить актуальность основания. Подготовьте контекст заново; прежний пакет не изменён.'));}
          finally{if(current())button.disabled=false;}
        };
        card.append(button,output);
      });root.append(card);
    }
  }
  return {render,reset};
}
