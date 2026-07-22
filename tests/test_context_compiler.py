from __future__ import annotations
import tempfile
import unittest
import http.client
import hashlib
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from gaia.provenance import ProvenanceStore, ProvenanceError
from gaia.protection import protect
from gaia.review import ReviewService
from gaia.context_compiler import ContextCompiler, ContextService, validate_candidates
from gaia.controlled_intake import ControlledIntake
from gaia.server import Handler, SESSION_COOKIE_NAME, SESSION_TOKEN

class ContextCompilerTests(unittest.TestCase):
    def setup(self):
        tmp=tempfile.TemporaryDirectory(); s=ProvenanceStore(Path(tmp.name)); w=s.create_workspace(); src=s.accept_bytes(w,b"[PERSON_1] decided: use local review. Risk: delay.","text/plain"); ext=s.create_extraction(w,src["source_id"],"v1"); san=protect(s,w,ext["artifact_id"])["sanitized"]
        ReviewService(s,w,lambda text:{"findings":[]}).start(san["artifact_id"]); ReviewService(s,w).confirm(san["artifact_id"])
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

    def test_model_failure_is_safe_and_does_not_change_existing_context(self):
        tmp,s,w,san=self.setup()
        try:
            existing=ContextCompiler(s,w,lambda text:{"candidates":[{"type":"requirement","title":"Требование","statement":"Сохранить локальную проверку.","block":{"start":0,"end":8},"confidence":"high","requires_review":True}]}).compile(san["artifact_id"])[0]
            ContextService(s,w).decide(existing["id"],"confirm")
            with self.assertRaises(ProvenanceError): ContextCompiler(s,w,lambda text:(_ for _ in ()).throw(RuntimeError("synthetic failure"))).compile(san["artifact_id"],compiler_version="context-v2")
            self.assertEqual(ContextService(s,w).get(existing["id"])["status"],"confirmed")
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
            ReviewService(s,w,lambda text:{"findings":[]}).start(san2["artifact_id"]); ReviewService(s,w).confirm(san2["artifact_id"])
            second=ContextCompiler(s,w,model).compile(san2["artifact_id"])
            action=next(x for x in second if x["item_type"]=="action")
            self.assertEqual(len(action["source_links"]),2)
            # A conflicting decision remains separate and cannot displace the confirmed old one.
            conflict=ContextCompiler(s,w,lambda text:{"candidates":[{**model(text)["candidates"][0],"statement":"Использовать иной локальный маршрут."}]}).compile(san2["artifact_id"],compiler_version="context-v2")[0]
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
                self.assertEqual(status,202); self.assertEqual(len(data["candidates"]),5)
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
