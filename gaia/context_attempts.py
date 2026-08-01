"""Durable, content-free latest state for context compilation."""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any
from .storage import atomic_write_text, path_lock

TERMINAL = {"done", "complete_empty", "failed", "cancelled", "interrupted"}
# model_attempts is the legacy count of semantic units entered; model_call_count includes retries.
SAFE_FIELDS = ("status","phase","error_code","diagnostic_code","created_at","started_at","updated_at","last_activity_at","finished_at","current_chunk","completed_chunks","total_chunks","progress","model_attempts","model_call_count","last_unit_attempts","candidate_count","cancellation_requested")
def safe_message(status: str, error_code: str = "") -> str:
    code=error_code.upper()
    if status=="complete_empty": return "Сборка завершена успешно, но элементов проектного контекста не найдено."
    if status=="done": return "Контекст собран. Проверьте кандидатов."
    if status=="cancelled": return "Сборка отменена. Контекст не изменён."
    if status=="interrupted": return "Предыдущая сборка была прервана. Контекст не изменён."
    if "EVIDENCE" in code: return "Не удалось однозначно подтвердить фрагмент материала. Контекст не создан."
    if "TIMEOUT" in code: return "Сборка остановлена по лимиту времени. Контекст не создан."
    if "CHUNK_LIMIT" in code or "CONTEXT_LIMIT" in code: return "Материал содержит слишком много смысловых единиц. Разделите его на части."
    if "MODEL" in code or "PROVIDER" in code: return "Локальная модель недоступна или не завершила обработку. Контекст не создан."
    if "JSON" in code or "SCHEMA" in code or "INVALID" in code: return "Ответ локальной модели не прошёл проверку. Контекст не создан."
    return "Не удалось завершить сборку проектного контекста. Контекст не изменён."
class ContextAttemptStore:
    def __init__(self, store: Any): self.store=store; self.path=store.root/"metadata"/"context_compile_attempts.json"
    def _key(self,w:str,a:str)->str:return f"{w}:{a}"
    def _read(self)->dict[str,Any]:return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"attempts":{}}
    def save(self,w:str,a:str,values:dict[str,Any])->dict[str,Any]:
        with path_lock(self.path):
            data=self._read(); record=dict(data.setdefault("attempts",{}).get(self._key(w,a),{})); record.update({k:values[k] for k in SAFE_FIELDS if k in values}); record["workspace_id"],record["artifact_id"]=w,a; data["attempts"][self._key(w,a)]=record; atomic_write_text(self.path,json.dumps(data,ensure_ascii=False,indent=2)+"\n")
        return record
    def get(self,w:str,a:str,active:dict[str,Any]|None=None)->dict[str,Any]|None:
        if active:return dict(active)
        record=self._read().get("attempts",{}).get(self._key(w,a))
        if not record:return None
        if record.get("status") not in TERMINAL:
            now=datetime.now().isoformat(timespec="seconds"); record=self.save(w,a,{**record,"status":"interrupted","phase":"interrupted","error_code":"CONTEXT_INTERRUPTED","finished_at":now,"updated_at":now,"last_activity_at":now})
        return record
