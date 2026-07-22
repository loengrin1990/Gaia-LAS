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
