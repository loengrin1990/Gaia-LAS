from __future__ import annotations
import tempfile
import unittest
import http.client
import hashlib
import json
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from gaia.provenance import ProvenanceStore, ProvenanceError
from gaia.protection import protect
from gaia.review import ReviewService
from gaia.context_compiler import CandidateValidationError, ContextCompiler, ContextService, validate_candidates
from gaia.context_compiler import ContextCompileError, local_context_model
from gaia.controlled_intake import ControlledIntake
from gaia.server import Handler, SESSION_COOKIE_NAME, SESSION_TOKEN

class ContextCompilerTests(unittest.TestCase):
    def setup(self):
        tmp=tempfile.TemporaryDirectory(); s=ProvenanceStore(Path(tmp.name)); w=s.create_workspace(); src=s.accept_bytes(w,b"[PERSON_1] decided: use local review. Risk: delay.","text/plain"); ext=s.create_extraction(w,src["source_id"],"v1"); san=protect(s,w,ext["artifact_id"])["sanitized"]
        ReviewService(s,w,lambda text:{"status":"completed","findings":[]}).start(san["artifact_id"]); ReviewService(s,w).confirm(san["artifact_id"])
        return tmp,s,w,san
    def test_compiles_confirmed_only_idempotently_and_preserves_provenance(self):
        tmp,s,w,san=self.setup()
        try:
            seen=[]
            def model(text):
                seen.append(text); return {"candidates":[
                    {"type":"requirement","title":"Локальная проверка","statement":"Использовать локальную проверку.","block":{"start":0,"end":10},"confidence":"high","requires_review":True},
                    {"type":"decision","title":"Проверка","statement":"Решение использовать локальную проверку.","block":{"start":0,"end":12},"confidence":"medium","requires_review":True},
                    {"type":"risk","title":"Задержка","statement":"Есть риск задержки.","block":{"start":0,"end":8},"confidence":"medium","requires_review":True},
                    {"type":"open_question","title":"Срок","statement":"Срок не указан.","block":{"start":0,"end":5},"confidence":"low","requires_review":True},
                    {"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":6},"confidence":"medium","requires_review":True}]}
            compiler=ContextCompiler(s,w,model); items=compiler.compile(san["artifact_id"])
            self.assertEqual(len(items),5); self.assertEqual(seen, ["[PERSON_1] decided: use local review. Risk: delay."])
            self.assertEqual(len(compiler.compile(san["artifact_id"])),5)
            service=ContextService(s,w); confirmed=service.decide(items[0]["id"],"confirm"); self.assertEqual(service.summary()["requirement"][0]["title"], confirmed["title"])
            edited=service.decide(confirmed["id"],"edit","Новая версия","Уточнённое требование."); self.assertTrue(edited["current"]); self.assertFalse(s.object_metadata(w,confirmed["id"])["current"])
            with self.assertRaisesRegex(ProvenanceError, "устарела"):
                service.decide(confirmed["id"], "reject")
        finally: tmp.cleanup()
    def test_rejects_unconfirmed_and_invalid_model_result(self):
        tmp,s,w,san=self.setup()
        try:
            ReviewService(s,w).get(san["artifact_id"])
            with self.assertRaises(ProvenanceError): validate_candidates({"candidates":[{"type":"unknown"}]},20)
            newer=protect(s,w,s.object_metadata(w,san["artifact_id"])["parents"][0],rules_version="v2")["sanitized"]
            with self.assertRaises(ProvenanceError): ContextCompiler(s,w,lambda text:{"candidates":[]}).compile(newer["artifact_id"])
        finally: tmp.cleanup()

    def test_validation_rejects_bad_blocks_unknown_fields_and_large_answers(self):
        valid={"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":5},"confidence":"low","requires_review":True}
        with self.assertRaises(ProvenanceError): validate_candidates({"candidates":[{**valid,"extra":"no"}]},20)
        with self.assertRaises(ProvenanceError): validate_candidates({"candidates":[{**valid,"block":{"start":0,"end":21}}]},20)
        with self.assertRaises(ProvenanceError): validate_candidates({"candidates":[valid]*33},20)

    def test_validator_rejects_the_real_model_shape_without_writing_candidates(self):
        valid={"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":5},"confidence":"low","requires_review":True}
        cases = [
            ({"candidates":[{**valid,"confidence":1}]}, "schema_field"),
            ({"candidates":[{**valid,"requires_review":None,"requires review":True}]}, "schema_unknown_field"),
            ({"candidates":[{key:value for key,value in valid.items() if key != "requires_review"}]}, "schema_required_fields"),
            ({"candidates":[{**valid,"type":"требование"}]}, "unknown_type"),
            ({"candidates":[{**valid,"type":"solution"}]}, "unknown_type"),
            ({"candidates":[valid,{**valid,"title":42}]}, "schema_field"),
        ]
        for payload, code in cases:
            with self.subTest(code=code), self.assertRaises(CandidateValidationError) as rejected:
                validate_candidates(payload,20)
            self.assertEqual(rejected.exception.diagnostic_code, code)
        tmp,s,w,san=self.setup()
        try:
            runtime_shape={"candidates":[{**valid,"confidence":1}]}
            with self.assertRaises(ContextCompileError) as rejected:
                ContextCompiler(s,w,lambda text:runtime_shape).compile(san["artifact_id"])
            self.assertEqual(rejected.exception.code,"local_model_invalid")
            self.assertEqual(rejected.exception.diagnostic_code,"schema_field")
            self.assertEqual(ContextService(s,w).list(),[])
        finally: tmp.cleanup()

    def test_model_failure_is_safe_and_does_not_change_existing_context(self):
        tmp,s,w,san=self.setup()
        try:
            existing=ContextCompiler(s,w,lambda text:{"candidates":[{"type":"requirement","title":"Требование","statement":"Сохранить локальную проверку.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}]}).compile(san["artifact_id"])[0]
            ContextService(s,w).decide(existing["id"],"confirm")
            with self.assertRaises(ProvenanceError): ContextCompiler(s,w,lambda text:(_ for _ in ()).throw(RuntimeError("synthetic failure"))).compile(san["artifact_id"],compiler_version="context-v3")
            self.assertEqual(ContextService(s,w).get(existing["id"])["status"],"confirmed")
        finally: tmp.cleanup()

    def test_large_material_retries_then_splits_without_partial_records(self):
        tmp,s,w,san=self.setup()
        try:
            path=s.root / "sanitized" / w / f"{san['artifact_id']}.txt"
            path.write_text((("Требование: проверить локальную версию. " + "деталь " * 70 + "\n\n") * 30), encoding="utf-8")
            calls=[]
            def model(text):
                calls.append(len(text))
                if len(text) > 1200: return {"candidates": [{"type":"requirement","title":"Слишком много","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}] * 16}
                return {"candidates":[{"type":"requirement","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"medium","requires_review":True}]}
            items=ContextCompiler(s,w,model).compile(san["artifact_id"])
            self.assertEqual(len(items),1); self.assertTrue(any(value > 1200 for value in calls)); self.assertGreater(len(calls), 2)
            self.assertGreaterEqual(items[0]["block_links"][0]["start"],0)
            self.assertLessEqual(items[0]["block_links"][0]["end"],len(path.read_text(encoding="utf-8")))
        finally: tmp.cleanup()

    def test_context_model_uses_dedicated_route_and_classifies_bad_response(self):
        with patch("gaia.context_compiler.execute_context_model_call", return_value={"ok":False}):
            with self.assertRaises(ContextCompileError) as unavailable: local_context_model("[Сотрудник-01]")
        self.assertEqual(unavailable.exception.code, "local_model_unavailable")
        with patch("gaia.context_compiler.execute_context_model_call", return_value={"ok":True,"answer":"not json"}) as call:
            with self.assertRaises(ContextCompileError) as invalid: local_context_model("[Сотрудник-01]")
        self.assertEqual(invalid.exception.code, "local_model_invalid")
        self.assertEqual(call.call_args.args[1], 240)
        self.assertEqual(call.call_args.args[0]["task"], "context_compiler")
        self.assertEqual(call.call_args.args[0]["timeout"], 240)
        self.assertIsNone(call.call_args.args[0]["response_schema"])
        self.assertIn("Разрешённые необязательные поля", call.call_args.args[0]["prompt"])
        self.assertIn("не придумывай ответственного", call.call_args.args[0]["prompt"])
        self.assertIn("Сопоставление optional metadata", call.call_args.args[0]["prompt"])
        self.assertIn("actor_ref «[Координатор-Север]»", call.call_args.args[0]["prompt"])
        self.assertIn("deadline «15 сентября 2026 года»", call.call_args.args[0]["prompt"])
        from gaia.context_compiler import context_response_schema
        candidate = context_response_schema(16)["properties"]["candidates"]["items"]
        self.assertNotIn("actor_ref", candidate["required"])
        self.assertNotIn("deadline", candidate["required"])
        self.assertNotIn("status", candidate["required"])
        self.assertNotIn("priority", candidate["required"])
        with patch("gaia.context_compiler.execute_context_model_call", return_value={"ok":True,"answer":""}):
            with self.assertRaises(ContextCompileError) as empty: local_context_model("[Сотрудник-01]")
        self.assertEqual(empty.exception.diagnostic_code,"empty_response")
        with patch("gaia.context_compiler.execute_context_model_call", return_value={"ok":True,"answer":"{\"candidates\":[", "done_reason":"length", "eval_count":2400}):
            with self.assertRaises(ContextCompileError) as truncated: local_context_model("[Сотрудник-01]")
        self.assertEqual(truncated.exception.diagnostic_code, "output_truncated")

    def test_context_model_can_use_json_mode_without_weakening_backend_validation(self):
        route={"task":"context_compiler","provider":"test","model":"gpt-oss:20b","structured_output":"json","max_candidates_per_chunk":16,"timeout_seconds":120}
        answer={"candidates":[{"type":"action","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}]}
        with patch("gaia.context_compiler.resolve_route",return_value=route), patch("gaia.context_compiler.execute_context_model_call",return_value={"ok":True,"answer":json.dumps(answer)}) as call:
            self.assertEqual(local_context_model("Проверить материал."),answer)
        self.assertIsNone(call.call_args.args[0]["response_schema"])

    def test_optional_fields_are_preserved_only_when_model_supplies_them(self):
        tmp,s,w,san=self.setup()
        try:
            (s.root / "sanitized" / w / f"{san['artifact_id']}.txt").write_text("Роль 1 назначено до 2030-01-01. Приоритет высокий.", encoding="utf-8")
            payload={"candidates":[{"type":"action","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True,"actor_ref":"Роль 1","deadline":"2030-01-01","status":"назначено","priority":"высокий"}]}
            item=ContextCompiler(s,w,lambda _:payload).compile(san["artifact_id"])[0]
            self.assertEqual(item["actor_ref"],"Роль 1"); self.assertEqual(item["deadline"],"2030-01-01"); self.assertEqual(item["explicit_status"],"назначено"); self.assertEqual(item["priority"],"высокий")
            confirmed=ContextService(s,w).decide(item["id"],"confirm")
            edited=ContextService(s,w).decide(confirmed["id"],"edit","Уточнённая проверка","Проверить уточнённый материал.")
            self.assertEqual(edited["actor_ref"],"Роль 1"); self.assertEqual(edited["deadline"],"2030-01-01")
            self.assertEqual(edited["explicit_status"],"назначено"); self.assertEqual(edited["priority"],"высокий")
        finally: tmp.cleanup()

    def test_optional_metadata_not_present_in_cleaned_fragment_is_rejected(self):
        tmp,s,w,san=self.setup()
        try:
            payload={"candidates":[{"type":"action","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True,"actor_ref":"Иван Иванов"}]}
            with self.assertRaises(ContextCompileError) as rejected:
                ContextCompiler(s,w,lambda _:payload).compile(san["artifact_id"])
            self.assertEqual(rejected.exception.diagnostic_code,"metadata_not_in_fragment")
            self.assertEqual(ContextService(s,w).list(),[])
        finally: tmp.cleanup()

    def test_safe_alias_from_cleaned_text_is_preserved_as_actor_ref(self):
        tmp,s,w,san=self.setup()
        try:
            (s.root / "sanitized" / w / f"{san['artifact_id']}.txt").write_text("[Координатор-Север] согласует материал.", encoding="utf-8")
            payload={"candidates":[{"type":"action","title":"Согласовать","statement":"Согласовать материал.","block":{"start":0,"end":11},"confidence":"high","requires_review":True,"actor_ref":"[Координатор-Север]"}]}
            result=ContextCompiler(s,w,lambda _:payload).compile(san["artifact_id"])
            self.assertEqual(result[0]["actor_ref"],"[Координатор-Север]")
        finally: tmp.cleanup()

    def test_receipt_restores_exact_duplicates_after_restart(self):
        tmp,s,w,san=self.setup()
        try:
            payload={"candidates":[{"type":"action","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}]}
            first=ContextCompiler(s,w,lambda _:payload).compile(san["artifact_id"])
            source=s.object_metadata(w,san["parents"][0])["parents"][0]; ext=s.create_extraction(w,source,"v2"); san2=protect(s,w,ext["artifact_id"],rules_version="v2")["sanitized"]
            ReviewService(s,w,lambda _: {"status":"completed","findings":[]}).start(san2["artifact_id"]); ReviewService(s,w).confirm(san2["artifact_id"])
            calls=[]
            second=ContextCompiler(s,w,lambda text:(calls.append(text) or payload)).compile(san2["artifact_id"])
            again=ContextCompiler(ProvenanceStore(s.root),w,lambda _:(_ for _ in ()).throw(AssertionError("model must not run"))).compile(san2["artifact_id"])
            self.assertTrue(calls); self.assertEqual([x["id"] for x in second],[x["id"] for x in again]); self.assertEqual([x["id"] for x in first],[x["id"] for x in second]); self.assertIn(san2["artifact_id"],second[0]["source_links"])
        finally: tmp.cleanup()

    def test_empty_complete_receipt_is_idempotent(self):
        tmp,s,w,san=self.setup()
        try:
            first=ContextCompiler(s,w,lambda _: {"candidates":[]}).compile(san["artifact_id"])
            again=ContextCompiler(ProvenanceStore(s.root),w,lambda _: (_ for _ in ()).throw(AssertionError("model must not run"))).compile(san["artifact_id"])
            self.assertEqual(first,[]); self.assertEqual(again,[])
        finally: tmp.cleanup()

    def test_local_model_lifecycle_unloads_once_after_success_and_empty_success(self):
        for payload in ({"candidates":[]}, {"candidates":[{"type":"action","title":"Проверка","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}]}):
            with self.subTest(empty=not payload["candidates"]):
                tmp,s,w,san=self.setup()
                try:
                    compiler=ContextCompiler(s,w)
                    compiler.model=lambda text, cancel_event=None: payload
                    with patch.object(compiler,"_preload") as preload, patch.object(compiler,"_unload") as unload:
                        compiler.compile(san["artifact_id"])
                    preload.assert_called_once(); unload.assert_called_once_with(compiler._route(), "success")
                finally: tmp.cleanup()

    def test_local_model_lifecycle_unloads_after_preload_model_schema_and_persistence_failures(self):
        cases=[]
        cases.append(("preload", lambda compiler: patch.object(compiler,"_preload",side_effect=ContextCompileError("local_model_unavailable","safe","model_load_timeout"))))
        cases.append(("model", lambda compiler: patch.object(compiler,"_compile_chunk",side_effect=ContextCompileError("local_model_unavailable","safe","model_timeout"))))
        cases.append(("schema", lambda compiler: patch.object(compiler,"_compile_chunk",side_effect=ContextCompileError("local_model_invalid","safe","json_parse"))))
        cases.append(("persistence", lambda compiler: patch.object(compiler,"_persist_all",side_effect=RuntimeError("synthetic"))))
        for name, operation in cases:
            with self.subTest(name=name):
                tmp,s,w,san=self.setup()
                try:
                    compiler=ContextCompiler(s,w); compiler.model=lambda text, cancel_event=None: {"candidates":[]}
                    with operation(compiler), patch.object(compiler,"_unload") as unload:
                        with self.assertRaises(Exception): compiler.compile(san["artifact_id"])
                    unload.assert_called_once()
                finally: tmp.cleanup()

    def test_cancellation_between_chunks_unloads_without_persistence(self):
        tmp,s,w,san=self.setup()
        try:
            compiler=ContextCompiler(s,w); cancelled=threading.Event(); calls=[]
            def model(text, cancel_event=None):
                calls.append(text); cancelled.set()
                return {"candidates":[]}
            compiler.model=model
            chunks=[__import__("gaia.context_chunking",fromlist=["ContextChunk"]).ContextChunk(0,"first",0,5,"test"), __import__("gaia.context_chunking",fromlist=["ContextChunk"]).ContextChunk(1,"second",5,11,"test")]
            with patch("gaia.context_compiler.split_context",return_value=chunks), patch.object(compiler,"_persist_all") as persist, patch.object(compiler,"_unload") as unload:
                with self.assertRaises(ContextCompileError) as stopped: compiler.compile(san["artifact_id"],cancel_event=cancelled)
            self.assertEqual(stopped.exception.code,"cancelled"); self.assertEqual(len(calls),1); persist.assert_not_called(); unload.assert_called_once()
        finally: tmp.cleanup()

    def test_receipt_fast_path_skips_local_model_lifecycle(self):
        tmp,s,w,san=self.setup()
        try:
            ContextCompiler(s,w,lambda text: {"candidates":[]}).compile(san["artifact_id"])
            compiler=ContextCompiler(s,w)
            with patch.object(compiler,"_preload") as preload, patch.object(compiler,"_unload") as unload:
                self.assertEqual(compiler.compile(san["artifact_id"]),[])
            preload.assert_not_called(); unload.assert_not_called()
        finally: tmp.cleanup()

    def test_unload_failure_never_masks_compiler_outcome(self):
        tmp,s,w,san=self.setup()
        try:
            compiler=ContextCompiler(s,w); compiler.model=lambda text, cancel_event=None: {"candidates":[]}
            with patch.object(compiler,"_preload"), patch("gaia.context_compiler.provider_config",return_value={"type":"ollama","endpoint":"http://127.0.0.1:1"}), patch("gaia.context_compiler.execute_context_model_call",side_effect=RuntimeError("unavailable")):
                self.assertEqual(compiler.compile(san["artifact_id"]),[])
            compiler=ContextCompiler(s,w); compiler.model=lambda text, cancel_event=None: (_ for _ in ()).throw(ContextCompileError("local_model_invalid","safe","json_parse"))
            with patch.object(compiler,"_preload"), patch("gaia.context_compiler.provider_config",return_value={"type":"ollama","endpoint":"http://127.0.0.1:1"}), patch("gaia.context_compiler.execute_context_model_call",side_effect=RuntimeError("unavailable")):
                with self.assertRaises(ContextCompileError) as failed: compiler.compile(san["artifact_id"],compiler_version="context-v3")
            self.assertEqual(failed.exception.diagnostic_code,"json_parse")
        finally: tmp.cleanup()

    def test_duplicate_conflict_filters_and_workspace_isolation_survive_restart(self):
        tmp,s,w,san=self.setup()
        try:
            def model(text):
                return {"candidates":[
                    {"type":"decision","title":"Маршрут","statement":"Использовать локальный маршрут.","block":{"start":0,"end":8},"confidence":"high","requires_review":True},
                    {"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"medium","requires_review":True},
                ]}
            first=ContextCompiler(s,w,model).compile(san["artifact_id"])
            service=ContextService(s,w); service.decide(first[0]["id"],"confirm")
            # A second confirmed material carrying the exact same action adds provenance, not a copy.
            source_id=s.object_metadata(w,san["parents"][0])["parents"][0]
            ext=s.create_extraction(w,source_id,"v2")
            san2=protect(s,w,ext["artifact_id"],rules_version="v2")["sanitized"]
            ReviewService(s,w,lambda text:{"status":"completed","findings":[]}).start(san2["artifact_id"]); ReviewService(s,w).confirm(san2["artifact_id"])
            second=ContextCompiler(s,w,model).compile(san2["artifact_id"])
            action=next(x for x in second if x["item_type"]=="action")
            self.assertEqual(len(action["source_links"]),2)
            # A conflicting decision remains separate and cannot displace the confirmed old one.
            conflict=ContextCompiler(s,w,lambda text:{"candidates":[{**model(text)["candidates"][0],"statement":"Использовать иной локальный маршрут."}]}).compile(san2["artifact_id"],compiler_version="context-v3")[0]
            self.assertEqual(s.object_metadata(w,first[0]["id"])["status"],"confirmed")
            self.assertEqual(ContextService(s,w).get(conflict["id"])["status"],"conflicted")
            service.resolve_conflict(conflict["id"],"keep_both")
            self.assertEqual(len(service.summary({"type":"decision","conflict":"true"})["decision"]),2)
            self.assertEqual(service.decide(first[1]["id"],"edit","Уточнить","Уточнить материал.")["version"],2)
            other=s.create_workspace()
            with self.assertRaises(ProvenanceError): ContextService(s,other).get(first[0]["id"])
            reopened=ProvenanceStore(Path(tmp.name))
            self.assertEqual(reopened.object_metadata(w,conflict["id"])["confirmation_status"],"confirmed")
        finally: tmp.cleanup()

    def test_duplicate_marking_and_no_optional_invention(self):
        tmp,s,w,san=self.setup()
        try:
            result=ContextCompiler(s,w,lambda text:{"candidates":[
                {"type":"risk","title":"Риск","statement":"Есть риск задержки.","block":{"start":0,"end":6},"confidence":"medium","requires_review":True},
                {"type":"risk","title":"Риск копия","statement":"Другой риск задержки.","block":{"start":0,"end":6},"confidence":"medium","requires_review":True},
            ]}).compile(san["artifact_id"])
            self.assertNotIn("actor_ref",result[0]); self.assertNotIn("deadline",result[0]); self.assertNotIn("reason",result[0])
            service=ContextService(s,w); service.decide(result[0]["id"],"confirm")
            service.mark_duplicate(result[1]["id"],result[0]["id"])
            self.assertEqual(service.get(result[1]["id"])["confirmation_status"],"duplicate")
            self.assertFalse(service.get(result[1]["id"])["current"])
        finally: tmp.cleanup()

    def test_explicit_optional_status_and_proposed_relations_are_safe_metadata(self):
        tmp,s,w,san=self.setup()
        try:
            item=ContextCompiler(s,w,lambda text:{"candidates":[{"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":6},"confidence":"medium","requires_review":True,"status":"назначено","relations":["Локальная проверка"]}]}).compile(san["artifact_id"])[0]
            self.assertEqual(item["status"],"requires_review")
            self.assertEqual(item["explicit_status"],"назначено")
            self.assertEqual(item["proposed_relations"],["Локальная проверка"])
        finally: tmp.cleanup()

    def test_loopback_http_flow_compiles_and_reviews_safe_candidates(self):
        tmp,s,w,san=self.setup()
        server=None
        try:
            project="synthetic-http"
            intake=ControlledIntake(s)
            mapping=intake._read(); mapping["workspaces"][hashlib.sha256(project.encode()).hexdigest()]=w
            intake.path.write_text(json.dumps(mapping),encoding="utf-8")
            self.assertEqual(intake._workspace_for(project),w)
            fake_result={"candidates":[
                {"type":"requirement","title":"Локальная проверка","statement":"Использовать локальную проверку.","block":{"start":0,"end":8},"confidence":"high","requires_review":True},
                {"type":"decision","title":"Маршрут","statement":"Оставить локальный маршрут.","block":{"start":0,"end":8},"confidence":"medium","requires_review":True},
                {"type":"risk","title":"Задержка","statement":"Есть риск задержки.","block":{"start":0,"end":8},"confidence":"low","requires_review":True},
                {"type":"open_question","title":"Срок","statement":"Срок не указан.","block":{"start":0,"end":8},"confidence":"low","requires_review":True},
                {"type":"action","title":"Проверить","statement":"Проверить материал.","block":{"start":0,"end":8},"confidence":"medium","requires_review":True},
            ]}
            with patch("gaia.controlled_intake.default_store",return_value=s), patch("gaia.context_compiler.local_context_model",return_value=fake_result):
                server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
                port=server.server_address[1]
                def request(method,path,payload=None):
                    connection=http.client.HTTPConnection("127.0.0.1",port,timeout=3)
                    body=json.dumps(payload).encode() if payload is not None else None
                    headers={"Host":f"127.0.0.1:{port}","Origin":f"http://127.0.0.1:{port}","Cookie":f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}","Content-Type":"application/json"}
                    connection.request(method,path,body,headers); response=connection.getresponse(); data=json.loads(response.read()); connection.close(); return response.status,data
                status,data=request("POST",f"/api/context/{san['artifact_id']}/compile",{"project":project})
                self.assertEqual(status,202); self.assertEqual(data["status"], "queued")
                for _ in range(30):
                    status, job = request("GET", data["status_url"])
                    if job["status"] in {"done", "failed", "cancelled"}: break
                    time.sleep(0.03)
                self.assertEqual(job["status"], "done"); self.assertEqual(len(job["result"]["candidates"]),5)
                status,listed=request("GET",f"/api/context?project={project}")
                self.assertEqual(status,200); self.assertEqual(len(listed["candidates"]),5)
                candidate_id=listed["candidates"][0]["id"]
                self.assertEqual(request("POST",f"/api/context/{candidate_id}/decision",{"project":project,"decision":"confirm"})[0],200)
                self.assertEqual(request("GET",f"/api/context/summary?project={project}")[0],200)
                other=ControlledIntake(s); other._workspace_for("other-http")
                self.assertEqual(request("GET",f"/api/context/{candidate_id}?project=other-http")[0],404)
        finally:
            if server: server.shutdown(); server.server_close()
            tmp.cleanup()
