/* Local read-only OC evidence viewer; no confirmation or export actions. */
function createOperationalEvidenceViewer({document, fetch, getProject}) {
  const dialog=document.getElementById('operationalEvidenceDialog');
  const status=document.getElementById('operationalEvidenceStatus');
  const content=document.getElementById('operationalEvidenceContent');
  let generation=0, controller=null, busyKey='';
  const clear=()=>{content.replaceChildren();status.textContent='';};
  function reset(){generation++;controller?.abort();controller=null;busyKey='';clear();}
  function close(){reset();if(dialog.open)dialog.close();}
  document.getElementById('operationalEvidenceClose').addEventListener('click',close);
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('close',()=>{if(!dialog.open)reset();});
  function paragraph(tag,text){const node=document.createElement(tag);node.textContent=text;content.appendChild(node);return node;}
  async function open(id,current=false){
    const project=getProject(), category=current?'current':'pending', key=project+'\n'+category+'\n'+id;
    if(busyKey===key)return false;
    reset();const token=generation;busyKey=key;controller=new AbortController();
    const active=()=>token===generation && project===getProject() && dialog.open;
    if(!dialog.open)dialog.showModal();
    status.textContent='Проверяем доступность исходной очищенной версии…';
    try {
      const response=await fetch(`/api/operational-context/review/${category}/${encodeURIComponent(id)}/evidence?project=${encodeURIComponent(project)}`,{signal:controller.signal,cache:'no-store'});
      const data=await response.json();
      if(!active())return false;
      if(!response.ok || data.precision!=='whole_material' || typeof data.statement!=='string' || typeof data.cleaned_text!=='string' || data.export_allowed!==false)throw new Error('unavailable');
      paragraph('h3',current?'Принятое утверждение':'Утверждение предложения');
      paragraph('p',data.statement);
      paragraph('h3','Полный очищенный материал');
      paragraph('p','Точный фрагмент для этой карточки не сохранён. Ниже показана конкретная очищенная версия, связанная с карточкой, целиком; это не выделенная цитата.');
      const text=paragraph('pre',data.cleaned_text);text.style.whiteSpace='pre-wrap';text.style.overflowWrap='anywhere';
      status.textContent='Версия и целостность проверены при открытии. Доступность материала не доказывает истинность утверждения. Просмотр не подтверждает его и не разрешает внешнюю передачу.';
      return true;
    } catch(_) {
      if(active()){content.replaceChildren();status.textContent='Основание недоступно: оно могло устареть, измениться или не иметь доступной очищенной версии. Другой материал не подставлен. Закройте просмотр и повторите открытие для новой проверки.';}
      return false;
    } finally {if(token===generation){busyKey='';controller=null;}}
  }
  return {open,close};
}
if(typeof module!=='undefined')module.exports={createOperationalEvidenceViewer};
