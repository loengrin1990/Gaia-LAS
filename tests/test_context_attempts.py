from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from gaia.context_attempts import ContextAttemptStore, safe_message
from gaia.provenance import ProvenanceStore

class ContextAttemptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=ProvenanceStore(Path(self.tmp.name)); self.workspace=self.store.create_workspace(); self.attempts=ContextAttemptStore(self.store)
    def tearDown(self): self.tmp.cleanup()
    def test_failed_attempt_persists_only_safe_fields(self):
        self.attempts.save(self.workspace,'san-old',{'status':'failed','phase':'compiling','error_code':'CONTEXT_JSON_PARSE','created_at':'2026-01-01T00:00:00','prompt':'secret','evidence_quote':'secret','error':'secret'})
        payload=json.loads(self.attempts.path.read_text(encoding='utf-8'))
        record=next(iter(payload['attempts'].values())); self.assertEqual(record['error_code'],'CONTEXT_JSON_PARSE'); self.assertNotIn('prompt',record); self.assertNotIn('evidence_quote',record); self.assertNotIn('error',record)
    def test_running_attempt_becomes_interrupted_and_stays_terminal(self):
        self.attempts.save(self.workspace,'san-old',{'status':'running','phase':'compiling'})
        first=self.attempts.get(self.workspace,'san-old'); second=self.attempts.get(self.workspace,'san-old')
        self.assertEqual(first['status'],'interrupted'); self.assertEqual(second['status'],'interrupted'); self.assertEqual(first['error_code'],'CONTEXT_INTERRUPTED')
    def test_attempt_is_bound_to_artifact_version(self):
        self.attempts.save(self.workspace,'san-old',{'status':'failed','error_code':'CONTEXT_TIMEOUT'})
        self.assertIsNone(self.attempts.get(self.workspace,'san-new'))
    def test_success_and_empty_are_not_interrupted(self):
        for artifact,status in [('san-done','done'),('san-empty','complete_empty')]:
            self.attempts.save(self.workspace,artifact,{'status':status})
            self.assertEqual(self.attempts.get(self.workspace,artifact)['status'],status)
    def test_safe_messages_hide_raw_details(self):
        self.assertIn('фрагмент',safe_message('failed','CONTEXT_EVIDENCE_AMBIGUOUS'))
        self.assertNotIn('raw model response',safe_message('failed','unknown'))
