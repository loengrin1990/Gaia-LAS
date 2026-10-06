"""Manual disclosure is bounded to a reviewed request, never a routing grant."""
import json
import unittest
import test_desk_working_memory as fixture
from gaia.context_desk import ContextDesk, DeskError


class ManualCopyTests(unittest.TestCase):
    setUp = fixture.WorkingMemoryTests.setUp
    prepare = fixture.WorkingMemoryTests.prepare

    def authorize(self, preview, **changes):
        payload = dict(id=preview['id'], digest=preview['digest'], reviewed=True,
                       decisions={r['key']: 'allowed' for r in preview['reviews']})
        payload.update(changes)
        return self.desk.authorize_memory_copy(payload)

    def test_complete_packet_and_request_bound_return(self):
        ident = self.prepare()
        before = self.desk.state_path.read_bytes()
        preview = self.desk.memory_export_preview({'id': ident})
        self.assertEqual(before, self.desk.state_path.read_bytes())
        self.assertIn(self.source.read_text(), preview['text'])
        self.assertIn('Сопоставить письмо', preview['text'])
        self.assertIn(ident, preview['text'])
        copied = self.authorize(preview)
        self.assertEqual(copied['text'], preview['text'])
        self.assertEqual(self.desk.working_memory()['version'], 0)
        result = {'outcome': 'update', 'memory': 'Запрошено подтверждение срока, ответа нет.',
                  'changes': [{'description': 'Добавлен вопрос', 'quote': self.source.read_text()}]}
        with self.assertRaises(DeskError):
            self.desk.submit_memory_reply({'id': ident, 'text': json.dumps(result)})
        with self.assertRaises(DeskError):
            self.desk.submit_memory_reply({'id': ident, 'text': json.dumps({'request_id': 'another', 'result': result})})
        response = self.desk.submit_memory_reply({'id': ident, 'text': '```json\n' + json.dumps({'request_id': ident, 'result': result}) + '\n```'})
        self.assertEqual(response['version'], 0)
        self.assertEqual(len(response['proposals']), 1)
        self.desk.decide_memory({'id': ident, 'decision': 'accept'})
        self.assertEqual(ContextDesk(self.profile, self.data).working_memory()['version'], 1)

    def test_incomplete_restricted_unknown_or_changed_review_fails_closed(self):
        preview = self.desk.memory_export_preview({'id': self.prepare()})
        before = self.desk.state_path.read_bytes()
        for changes in ({'reviewed': False}, {'reviewed': 1}, {'digest': 'wrong'}, {'decisions': {}},
                        {'decisions': {r['key']: 'restricted' for r in preview['reviews']}},
                        {'decisions': {r['key']: 'unknown' for r in preview['reviews']}}):
            with self.subTest(changes=changes), self.assertRaises(DeskError):
                self.authorize(preview, **changes)
        self.assertEqual(before, self.desk.state_path.read_bytes())

    def test_changed_source_blocks_copy_even_after_prior_permission(self):
        preview = self.desk.memory_export_preview({'id': self.prepare()})
        self.authorize(preview)
        self.source.write_text('Другая редакция письма.')
        with self.assertRaises(DeskError): self.authorize(preview)

    def test_old_memory_is_included_and_requires_its_own_review(self):
        first = self.prepare()
        self.desk.propose_memory({'id': first, 'text': 'Прежние подтверждённые сведения.'})
        self.desk.decide_memory({'id': first, 'decision': 'accept'})
        preview = self.desk.memory_export_preview({'id': self.prepare()})
        self.assertIn('Прежние подтверждённые сведения.', preview['text'])
        self.assertIn('memory', [r['key'] for r in preview['reviews']])
        decisions = {r['key']: 'allowed' for r in preview['reviews'] if r['key'] != 'memory'}
        with self.assertRaises(DeskError): self.authorize(preview, decisions=decisions)

    def test_foreign_and_closed_requests_cannot_export(self):
        ident = self.prepare()
        other = ContextDesk(self.profile, self.root / 'other')
        with self.assertRaises(DeskError): other.memory_export_preview({'id': ident})
        self.desk.submit_memory_reply({'id': ident, 'text': json.dumps({'outcome': 'no_changes', 'memory': '', 'changes': []})})
        with self.assertRaises(DeskError): self.desk.memory_export_preview({'id': ident})

    def test_new_memory_invalidates_old_preview(self):
        one, two = self.prepare(), self.prepare()
        preview = self.desk.memory_export_preview({'id': one})
        self.desk.propose_memory({'id': two, 'text': 'Новая принятая память.'})
        self.desk.decide_memory({'id': two, 'decision': 'accept'})
        with self.assertRaises(DeskError): self.authorize(preview)

    def test_user_facing_labels(self):
        from pathlib import Path
        html = Path('gaia/static/context_desk.html').read_text()
        for label in ('Передать контекст в сильный чат', 'Проверить пакет для чата',
                      'Разрешить и скопировать этот пакет', 'Разрешение не проверено'):
            self.assertIn(label, html)
        self.assertNotIn('innerHTML', html)
