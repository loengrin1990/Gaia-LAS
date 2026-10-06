import base64
import json
from unittest.mock import patch
import unittest
import test_desk_working_memory as fixture
from gaia.context_desk import DeskError, ContextDesk
from gaia.desk_analysis import validate_reply, local_analysis


class AnalysisCycleTests(unittest.TestCase):
    setUp = fixture.WorkingMemoryTests.setUp
    prepare = fixture.WorkingMemoryTests.prepare
    propose = fixture.WorkingMemoryTests.propose

    def test_structured_response_grounding_and_no_change(self):
        before = 'Срок неизвестен.'
        packet = 'Подтвердите срок до конца апреля.'
        result = {'outcome': 'update', 'memory': 'Участник задал вопрос о сроке.',
                  'changes': [{'description': 'Добавлен вопрос', 'quote': packet}]}
        self.assertEqual(validate_reply(json.dumps(result), before, packet), result)
        result['changes'][0]['quote'] = 'Срок подтверждён заказчиком.'
        with self.assertRaises(ValueError): validate_reply(json.dumps(result), before, packet)
        self.assertEqual(validate_reply(json.dumps({'outcome': 'no_changes', 'memory': 'Перефразировано', 'changes': []}), before, packet)['memory'], before)

    def test_upload_is_project_scoped_and_deduplicates_bytes(self):
        payload = {'name': 'письмо.txt', 'data': base64.b64encode('Проверить новый материал.'.encode()).decode()}
        first = self.desk.upload(payload)
        second = self.desk.upload({**payload, 'name': 'копия.txt'})
        self.assertEqual(first['id'], second['id']); self.assertTrue(second['duplicate'])
        reopened = ContextDesk(self.profile, self.data)
        self.assertEqual(reopened.preview(first['id'])['text'], first['text'])
        self.assertTrue(any(x['id'] == first['id'] for x in reopened.inventory()['items']))
        other = ContextDesk(self.profile, self.root / 'elsewhere')
        with self.assertRaises(DeskError): other.preview(first['id'])

    def test_invalid_upload_has_no_state_changes(self):
        state = self.desk.state_path.read_bytes()
        for payload in ({'name': 'a.txt', 'data': 'broken'}, {'name': 'a.exe', 'data': 'YQ=='}, {'name': 'a.txt', 'data': ''}):
            with self.assertRaises(DeskError): self.desk.upload(payload)
        self.assertEqual(state, self.desk.state_path.read_bytes())

    def test_imported_answer_remains_draft_with_grounding(self):
        ident = self.prepare()
        view = self.desk.memory_request_view({'id': ident})
        quote = 'Участник просит подтвердить срок. Ответа нет.'
        self.assertIn(quote, view['packet'])
        result = self.desk.submit_memory_reply({'id': ident, 'text': json.dumps({
            'outcome': 'update', 'memory': 'Запрошено уточнение срока, ответа нет.',
            'changes': [{'description': 'Добавлен вопрос о сроке', 'quote': quote}]})})
        self.assertEqual(result['text'], '')
        self.assertEqual(result['proposals'][0]['changes'][0]['quote'], quote)
        self.assertEqual(result['proposals'][0]['before'], '')
        self.desk.decide_memory({'id': ident, 'decision': 'accept'})
        accepted = self.desk.working_memory()
        self.assertIn('Запрошено', accepted['text'])
        self.assertEqual(accepted['history'][0]['text'], accepted['text'])
        self.assertIn(quote, accepted['history'][0]['packet'])
        self.assertEqual(ContextDesk(self.profile, self.data).working_memory()['history'], accepted['history'])

    def test_no_changes_closes_request_without_new_memory_version(self):
        ident = self.prepare()
        response = self.desk.submit_memory_reply({'id': ident, 'text': json.dumps({'outcome': 'no_changes', 'memory': '', 'changes': []})})
        self.assertTrue(response['no_changes']); self.assertEqual(response['version'], 0)
        with self.assertRaises(DeskError): self.propose(ident)

    def test_local_input_limit_precedes_network(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError): local_analysis('X' * 45001, '')
            opener.assert_not_called()

    def test_failed_reply_does_not_create_partial_proposal(self):
        ident = self.prepare()
        state = self.desk.state_path.read_bytes()
        with self.assertRaises(DeskError):
            self.desk.submit_memory_reply({'id': ident, 'text': json.dumps({
                'outcome': 'update', 'memory': 'Придуманное', 'changes': [{'description': 'Решение', 'quote': 'Согласовано всеми участниками.'}]})})
        self.assertEqual(state, self.desk.state_path.read_bytes())

    def test_ui_uses_human_labels_and_text_rendering(self):
        from pathlib import Path
        html = Path('gaia/static/context_desk.html').read_text()
        for label in ('Подготовить обновление памяти', 'Сопоставить локальной моделью', 'Подтвердить новую версию', 'Вернуться к подготовленному проходу', 'Добавлен в Gaia: '):
            self.assertIn(label, html)
        self.assertNotIn('innerHTML', html)
        self.assertNotIn('Вызовов моделей и автоматической отправки нет.', html)

    def test_background_analysis_produces_reviewable_proposal(self):
        import time
        ident = self.prepare()
        result = {'outcome': 'update', 'memory': 'Срок требует уточнения.',
                  'changes': [{'description': 'Вопрос', 'quote': 'Участник просит подтвердить срок. Ответа нет.'}]}
        with patch('gaia.desk_analysis.local_analysis', return_value=result):
            self.desk.start_memory_analysis({'id': ident})
            deadline = time.monotonic() + 3
            while self.desk.memory_job({'id': ident})['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertEqual(self.desk.memory_job({'id': ident})['state'], 'done')
        self.assertEqual(self.desk.working_memory()['version'], 0)
        self.assertEqual(len(self.desk.working_memory()['proposals']), 1)
