from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from gaia.provenance import ProvenanceStore
from gaia.protection import protect, safe_report

RAW = "mail test.person@example.invalid +7 900 000-00-00 8 (900) 000-00-00 https://internal.example.invalid/card?id=123 10.10.10.10 Bearer test_secret_token_123456 password=demo_key_123 123-456-789 00 7701234567 договор № AB-123 счёт 40702810000000000001 ул. Тестовая, дом 5"

class ProtectionTests(unittest.TestCase):
    def make(self):
        tmp=tempfile.TemporaryDirectory(); store=ProvenanceStore(Path(tmp.name)); workspace=store.create_workspace(); source=store.accept_bytes(workspace, RAW.encode(), "text/plain"); extraction=store.create_extraction(workspace, source["source_id"], "v1"); return tmp,store,workspace,source,extraction
    def test_categories_report_and_raw_values_are_safe(self):
        tmp,s,w,source,extraction=self.make()
        try:
            outcome=protect(s,w,extraction["artifact_id"], {"Сотрудник":["Иванов Иван Иванович"],"Организация":["Организация Альфа"],"Подразделение":["Отдел Тест"],"Проект":["Проект Секретный-123"],"Система":["Система Омега"]})
            cleaned=(s.root/"sanitized"/w/f"{outcome['sanitized']['artifact_id']}.txt").read_text(); report=str(outcome["report"])
            for value in ("test.person@example.invalid","test_secret_token_123456","40702810000000000001","10.10.10.10"):
                self.assertNotIn(value, cleaned); self.assertNotIn(value, report)
            self.assertFalse(outcome["sanitized"]["export_allowed"]); self.assertIn("ЭлектроннаяПочта", outcome["report"]["counts"])
            self.assertEqual(safe_report(s,w,outcome["sanitized"]["artifact_id"]),outcome["report"])
        finally: tmp.cleanup()
    def test_versions_pseudonyms_isolation_and_false_positives(self):
        tmp,s,w,source,extraction=self.make()
        try:
            first=protect(s,w,extraction["artifact_id"], rules_version="v1"); second=protect(s,w,extraction["artifact_id"], rules_version="v2")
            self.assertFalse(s.object_metadata(w,first["sanitized"]["artifact_id"])["current"]); self.assertTrue(second["sanitized"]["current"])
            other=s.create_workspace(); src=s.accept_bytes(other,b"test.person@example.invalid 2026-07-22 42 1000 RUB Stolitsa 127.0.0.1", "text/plain"); art=s.create_extraction(other,src["source_id"],"v1"); out=protect(s,other,art["artifact_id"]); body=(s.root/"sanitized"/other/f"{out['sanitized']['artifact_id']}.txt").read_text()
            self.assertIn("2026-07-22",body); self.assertIn("42",body); self.assertIn("127.0.0.1",body); self.assertIn("ЭлектроннаяПочта-01",body)
        finally: tmp.cleanup()
