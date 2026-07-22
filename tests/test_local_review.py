from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from gaia.provenance import ProvenanceStore, ProvenanceError
from gaia.protection import protect
from gaia.review import ReviewService, validate_model_payload

class LocalReviewTests(unittest.TestCase):
    def setup_review(self):
        tmp=tempfile.TemporaryDirectory(); store=ProvenanceStore(Path(tmp.name)); ws=store.create_workspace(); src=store.accept_bytes(ws,b"Clean Person-01 remains", "text/plain"); ext=store.create_extraction(ws,src["source_id"],"v1"); san=protect(store,ws,ext["artifact_id"])["sanitized"]
        return tmp,store,ws,src,ext,san
    def test_model_only_receives_cleaned_text_and_confirmation_is_versioned(self):
        tmp,s,w,src,ext,san=self.setup_review(); seen=[]
        try:
            def model(text): seen.append(text); return {"findings":[{"category":"Сотрудник","start":6,"end":15,"confidence":"medium","reason_code":"residual","requires_review":True}]}
            review=ReviewService(s,w,model); state=review.start(san["artifact_id"])
            self.assertEqual(seen, ["Clean Person-01 remains"]); self.assertIn("cleaned_text",state); self.assertNotIn("source",str(state))
            review.decide(san["artifact_id"],"model-1","keep"); self.assertEqual(review.confirm(san["artifact_id"]),"Clean Person-01 remains")
            newer=protect(s,w,ext["artifact_id"],rules_version="v2")["sanitized"]
            with self.assertRaises(ProvenanceError): review.confirm(san["artifact_id"])
            self.assertTrue(s.object_metadata(w,newer["artifact_id"])["current"])
        finally: tmp.cleanup()
    def test_rejects_invalid_model_payload_without_trusting_partial_result(self):
        tmp,s,w,src,ext,san=self.setup_review()
        try:
            review=ReviewService(s,w,lambda text:{"findings":[{"category":"Unknown","start":0,"end":2,"confidence":"high","reason_code":"x","requires_review":True}]})
            state=review.start(san["artifact_id"]); self.assertEqual(state["findings"],[]); self.assertFalse(state["confirmed"])
            with self.assertRaises(ProvenanceError): validate_model_payload({"findings":[{"category":"Сотрудник","start":5,"end":2,"confidence":"high","reason_code":"x","requires_review":True}]}, 10)
        finally: tmp.cleanup()

    def test_unexpected_model_failure_keeps_material_available_for_review(self):
        tmp,s,w,src,ext,san=self.setup_review()
        try:
            state=ReviewService(s,w,lambda text: (_ for _ in ()).throw(TimeoutError("synthetic timeout"))).start(san["artifact_id"])
            self.assertEqual(state["state"],"requires_review")
            self.assertEqual(state["findings"],[])
            self.assertFalse(state["confirmed"])
            self.assertTrue(s.object_metadata(w,san["artifact_id"])["current"])
        finally: tmp.cleanup()

    def test_successor_review_is_available_without_confirming_the_new_version(self):
        tmp,s,w,src,ext,san=self.setup_review()
        try:
            model=lambda text:{"findings":[]}
            review=ReviewService(s,w,model)
            review.start(san["artifact_id"])
            old_record=review._read()[san["artifact_id"]]
            old_record["decisions"]=[{"finding_id":"model-1","decision":"replace","category":"Сотрудник","created_at":"synthetic"}]
            review._write(san["artifact_id"], old_record)
            newer=protect(s,w,ext["artifact_id"],rules_version="v2")["sanitized"]
            successor=review.create_successor(san["artifact_id"], newer["artifact_id"])
            self.assertEqual(successor["artifact_id"], newer["artifact_id"])
            self.assertFalse(successor["confirmed"])
            self.assertEqual(len(successor["carried_decisions"]), 1)
            self.assertEqual(review.get(newer["artifact_id"])["artifact_id"], newer["artifact_id"])
            with self.assertRaises(ProvenanceError): review.confirm(san["artifact_id"])
        finally: tmp.cleanup()

    def test_rejects_pseudonym_findings_stale_coordinates_and_repeated_decisions(self):
        tmp,s,w,src,ext,san=self.setup_review()
        try:
            text=(s.root / "sanitized" / w / f"{san['artifact_id']}.txt").read_text(encoding="utf-8")
            with self.assertRaises(ProvenanceError):
                validate_model_payload({"findings":[{"category":"Сотрудник","start":7,"end":19,"confidence":"medium","reason_code":"residual","requires_review":True}]}, "prefix Сотрудник-01 suffix")
            review=ReviewService(s,w,lambda value:{"findings":[{"category":"Сотрудник","start":6,"end":15,"confidence":"medium","reason_code":"residual","requires_review":True}]})
            review.start(san["artifact_id"])
            review.decide(san["artifact_id"],"model-1","keep")
            with self.assertRaises(ProvenanceError): review.decide(san["artifact_id"],"model-1","keep")
            record=review._read()[san["artifact_id"]]; record["decisions"]=[]; record["findings"][0]["expected_fingerprint"]="other"; review._write(san["artifact_id"],record)
            with self.assertRaises(ProvenanceError): review.decide(san["artifact_id"],"model-1","replace")
            self.assertEqual((s.root / "sanitized" / w / f"{san['artifact_id']}.txt").read_text(encoding="utf-8"), text)
        finally: tmp.cleanup()

    def test_multiple_dictionary_replacements_keep_words_and_tokens_intact(self):
        tmp,s,w,src,ext,san=self.setup_review()
        try:
            source=s.accept_bytes(w, "Электронная почта: alpha@example.test. Сетевой адрес стенда: 10.1.2.3. Сотрудник Альфа Бета.".encode(), "text/plain")
            extraction=s.create_extraction(w,source["source_id"],"v1")
            cleaned=protect(s,w,extraction["artifact_id"],{"Сотрудник":["Альфа Бета"],"Другое":["стенда"]})["sanitized"]
            text=(s.root / "sanitized" / w / f"{cleaned['artifact_id']}.txt").read_text(encoding="utf-8")
            self.assertIn("Электронная почта",text); self.assertIn("Сетевой адрес",text)
            self.assertNotIn("Сотрудник-02отрудник",text)
            self.assertNotIn("Альфа Бета",text)
        finally: tmp.cleanup()

    def test_rejects_partial_word_finding_and_preserves_exact_synthetic_text(self):
        source = "Протокол по проекту «Сфера». Подразделение: Департамент. Сетевой адрес: 10.2.3.4. СНИЛС: 112-233-445 95. Договор № ТЕСТ-2026-0042."
        with self.assertRaises(ProvenanceError):
            validate_model_payload({"findings":[{"category":"Сотрудник","start":0,"end":1,"confidence":"high","reason_code":"residual","requires_review":True}]}, source)
        tmp,s,w,_,_,_=self.setup_review()
        try:
            src=s.accept_bytes(w,source.encode(),"text/plain"); ext=s.create_extraction(w,src["source_id"],"v1")
            san=protect(s,w,ext["artifact_id"],{"Сотрудник":["Сфера"]})["sanitized"]
            text=(s.root / "sanitized" / w / f"{san['artifact_id']}.txt").read_text(encoding="utf-8")
            for fragment in ("Протокол", "Подразделение", "Сетевой адрес", "СНИЛС", "Договор № ТЕСТ-2026-0042"):
                self.assertIn(fragment,text)
        finally: tmp.cleanup()

    def test_category_change_is_persisted_and_completes_review(self):
        tmp,s,w,_,_,san=self.setup_review()
        try:
            review=ReviewService(s,w,lambda value:{"findings":[{"category":"Сотрудник","start":6,"end":15,"confidence":"medium","reason_code":"residual","requires_review":True}]})
            review.start(san["artifact_id"])
            result=review.decide(san["artifact_id"],"model-1","change_category","Организация")
            self.assertEqual(result["findings"][0]["category"],"Организация")
            self.assertEqual(result["state"],"ready_for_confirmation")
            self.assertEqual(ReviewService(ProvenanceStore(Path(tmp.name)),w).get(san["artifact_id"])["findings"][0]["category"],"Организация")
            self.assertEqual(review.confirm(san["artifact_id"]),"Clean Person-01 remains")
        finally: tmp.cleanup()
