import base64
import copy
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import test_desk_working_memory as fixture
from gaia.addressed_memory import AddressedMemoryError, AddressedMemoryPilot, apply_operations
from gaia.context_desk import ContextDesk, make_handler


OLD = ('Срок пилота — 15 декабря 2026 года.\nКоординатор проверяет документы.\n'
       'Финансовый контролёр согласует бюджет.\nКоманда запускает пилот после подтверждения бюджета.\n'
       'Бюджет пока не подтверждён.')
NEW = 'Бюджет проекта подтверждён. Срок пилота остаётся 15 декабря 2026 года.'
QUOTE = 'Бюджет проекта подтверждён.'


class AddressedMemoryTests(unittest.TestCase):
    def setUp(self):
        fixture.WorkingMemoryTests.setUp(self)
        first = self.upload(OLD)
        request = self.desk.prepare_memory({'version': 0, 'task': 'Основа проекта', 'selected': [first]})
        self.desk.propose_memory({'id': request['id'], 'text': OLD})
        self.desk.decide_memory({'id': request['id'], 'decision': 'accept'})
        self.new = self.upload(NEW)
        self.pilot = AddressedMemoryPilot(self.desk)

    def upload(self, text):
        return self.desk.upload({'name': 'source.txt', 'data': base64.b64encode(text.encode()).decode()})

    def prepare(self):
        return self.pilot.prepare({'version': self.desk.working_memory()['version'], 'task': 'Обновить статус бюджета.', 'selected': [self.new]})

    def operation(self, **changes):
        return dict(action='replace', old_text='Бюджет пока не подтверждён.', new_text=QUOTE, quote=QUOTE, **changes)

    def propose(self, view, operations=None):
        return self.pilot.propose({'id': view['id'], 'reply': {'request_id': view['id'], 'base_version': view['version'],
            'operations': [self.operation()] if operations is None else operations}})

    def test_confirmed_selection_changes_only_addressed_text_and_survives_restart(self):
        view = self.prepare(); self.propose(view)
        self.assertEqual(self.desk.working_memory()['text'], OLD)
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        expected = OLD.replace('Бюджет пока не подтверждён.', QUOTE)
        self.assertEqual(preview['after'], expected)
        with self.assertRaises(AddressedMemoryError):
            self.pilot.decide({'id': view['id'], 'selected': [0], 'decision': 'accept'})
        self.pilot.decide({**preview, 'decision': 'accept'})
        restarted = ContextDesk(self.profile, self.data)
        self.assertEqual(restarted.working_memory()['text'], expected)
        self.assertEqual(restarted.working_memory()['version'], 2)
        self.assertEqual(len(restarted.working_memory()['history']), 2)
        self.assertEqual(self.upload(NEW)['id'], self.new['id'])
        self.assertEqual(len(restarted.state['uploads']), 2)

    def test_selected_subset_and_review_digest_bind_exact_changes(self):
        view = self.prepare(); extra = {'action': 'append', 'old_text': '', 'new_text': 'Дополнительная заметка.', 'quote': QUOTE}
        self.propose(view, [self.operation(), extra])
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        with self.assertRaises(AddressedMemoryError):
            self.pilot.decide({**preview, 'selected': [0, 1], 'decision': 'accept'})
        self.pilot.decide({**preview, 'decision': 'accept'})
        self.assertNotIn('Дополнительная', self.desk.working_memory()['text'])

    def test_ambiguous_overlapping_and_cascading_replacements(self):
        with self.assertRaises(AddressedMemoryError):
            apply_operations('aaa', [{'action': 'replace', 'old_text': 'aa', 'new_text': 'b', 'quote': QUOTE}], [NEW])
        with self.assertRaises(AddressedMemoryError):
            apply_operations('abcdef', [{'action': 'replace', 'old_text': old, 'new_text': 'x', 'quote': QUOTE} for old in ['abc', 'bcde']], [NEW])
        operations = [{'action': 'replace', 'old_text': 'FIRST', 'new_text': 'SECOND', 'quote': QUOTE},
                      {'action': 'replace', 'old_text': 'SECOND', 'new_text': 'THIRD', 'quote': QUOTE}]
        self.assertEqual(apply_operations('FIRST / SECOND', operations, [NEW])[0], 'SECOND / THIRD')

    def test_invalid_quote_action_and_noop_do_not_create_proposal(self):
        for change in [{'quote': 'Бюджет **проекта** подтверждён.'}, {'action': 'delete'}, {'new_text': 'Бюджет пока не подтверждён.'}]:
            with self.subTest(change=change):
                view = self.prepare(); operation = self.operation(); operation.update(change)
                before = self.desk.state_path.read_bytes()
                with self.assertRaises(AddressedMemoryError): self.propose(view, [operation])
                self.assertEqual(self.desk.state_path.read_bytes(), before)

    def test_task_is_not_source_evidence(self):
        view = self.pilot.prepare({'version': 1, 'task': 'Неподтверждённое решение о бюджете.', 'selected': [self.new]})
        operation = self.operation(); operation['quote'] = 'Неподтверждённое решение о бюджете.'
        with self.assertRaises(AddressedMemoryError): self.propose(view, [operation])

    def test_reply_is_bound_to_request_and_exact_integer_version(self):
        view = self.prepare()
        for identifier, version in [('foreign', 1), (view['id'], True), (view['id'], 0)]:
            with self.assertRaises(AddressedMemoryError):
                self.pilot.propose({'id': view['id'], 'reply': {'request_id': identifier, 'base_version': version, 'operations': [self.operation()]}})

    def test_stale_version_cannot_be_confirmed(self):
        view = self.prepare(); self.propose(view); preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        other = self.desk.prepare_memory({'version': 1, 'task': 'Рабочая заметка', 'selected': [self.new]})
        self.desk.propose_memory({'id': other['id'], 'text': OLD + '\nЗаметка.'})
        self.desk.decide_memory({'id': other['id'], 'decision': 'accept'})
        before = self.desk.working_memory()['text']
        with self.assertRaises(ValueError): self.pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(self.desk.working_memory()['text'], before)

    def test_changed_source_and_corrupt_frozen_memory_fail_closed(self):
        row = next(r for r in self.desk.inventory()['items'] if not r['id'].startswith('upload:'))
        reference = self.desk.preview(row['id'])
        view = self.pilot.prepare({'version': 1, 'task': 'Проверить письмо', 'selected': [reference]})
        self.source.write_text('Источник изменился.')
        with self.assertRaises(ValueError): self.pilot.inputs({'id': view['id']})
        view = self.prepare()
        request = next(r for r in self.desk.state['working_memory']['requests'] if r['id'] == view['id'])
        path = self.desk.store.root / 'sanitized' / self.desk.state['workspace'] / (request['previous'] + '.txt')
        path.write_text(OLD + ' Повреждение.')
        with self.assertRaises(AddressedMemoryError): self.pilot.inputs({'id': view['id']})

    def test_cancel_prepared_and_proposed_requests(self):
        for proposed in [False, True]:
            view = self.prepare()
            if proposed: self.propose(view)
            self.pilot.decide({'id': view['id'], 'decision': 'cancel'})
            with self.assertRaises(AddressedMemoryError): self.propose(view)
            with self.assertRaises(AddressedMemoryError): self.pilot.decide({'id': view['id'], 'decision': 'accept'})
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_cancel_while_model_runs_discards_late_result(self):
        entered, release = threading.Event(), threading.Event()
        view = self.prepare()
        def model(_view):
            entered.set(); release.wait(2)
            return {'request_id': view['id'], 'base_version': 1, 'operations': [self.operation()]}
        with patch('gaia.addressed_memory.local_operations', side_effect=model):
            self.pilot.analyze({'id': view['id']}); self.assertTrue(entered.wait(1))
            self.pilot.decide({'id': view['id'], 'decision': 'cancel'}); release.set()
            time.sleep(.05)
        self.assertEqual(self.pilot.dispatch('job', {'id': view['id']})['state'], 'cancelled')
        self.assertEqual(self.pilot.overview({})['proposals'], [])
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_no_changes_closes_request_without_new_version(self):
        view = self.prepare(); result = self.propose(view, [])
        self.assertTrue(result['no_changes']); self.assertEqual(result['version'], 1)
        with self.assertRaises(AddressedMemoryError): self.propose(view)

    def test_legacy_handlers_cannot_accept_or_rewrite_addressed_requests(self):
        view = self.prepare()
        with self.assertRaises(ValueError): self.desk.propose_memory({'id': view['id'], 'text': 'Другая память.'})
        self.propose(view)
        with self.assertRaises(ValueError): self.desk.decide_memory({'id': view['id'], 'decision': 'accept'})
        self.assertEqual(ContextDesk(self.profile, self.data).working_memory()['text'], OLD)

    def test_sensitive_new_text_cannot_bypass_final_protection(self):
        view = self.prepare(); operation = self.operation(); operation['new_text'] += ' Контакт: private@example.test'
        with self.assertRaises(AddressedMemoryError): self.propose(view, [operation])
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_final_protection_must_not_rewrite_unaddressed_text(self):
        view = self.prepare(); original = self.desk._managed
        def managed(store, workspace, text):
            if text.startswith('Срок пилота') and QUOTE in text:
                text = text.replace('Координатор', 'Изменённый координатор')
            return original(store, workspace, text)
        with patch.object(self.desk, '_managed', side_effect=managed):
            with self.assertRaises(AddressedMemoryError): self.propose(view)
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_http_opt_in_and_local_security_are_preserved(self):
        for enabled in [False, True]:
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.desk, addressed=enabled))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('GET', '/'); response = connection.getresponse(); html = response.read().decode()
                cookie = response.getheader('Set-Cookie').split(';', 1)[0]
                self.assertEqual('id="addressedPanel"' in html, enabled)
                connection.request('POST', '/api/desk/addressed/overview', '{}', {'Content-Type': 'application/json', 'Cookie': cookie, 'Origin': f'http://127.0.0.1:{server.server_port}'})
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 200 if enabled else 400)
                connection.request('POST', '/api/desk/addressed/overview', '{}', {'Content-Type': 'application/json'})
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
                connection.close()
            finally: server.shutdown(); server.server_close()

    def test_empty_legacy_memory_can_bootstrap_by_explicit_append(self):
        desk = ContextDesk(self.profile, self.root / 'empty-data')
        reference = desk.upload({'name': 'new.txt', 'data': base64.b64encode(NEW.encode()).decode()})
        pilot = AddressedMemoryPilot(desk)
        view = pilot.prepare({'version': 0, 'task': 'Создать первую память', 'selected': [reference]})
        pilot.propose({'id': view['id'], 'reply': {'request_id': view['id'], 'base_version': 0,
            'operations': [{'action': 'append', 'old_text': '', 'new_text': QUOTE, 'quote': QUOTE}]}})
        preview = pilot.selection({'id': view['id'], 'selected': [0]})
        pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(desk.working_memory()['text'], QUOTE)
        self.assertEqual(desk.working_memory()['version'], 1)

    def test_corrupt_operations_or_evidence_cannot_be_confirmed(self):
        for field in ['operations', 'evidence']:
            with self.subTest(field=field):
                view = self.prepare(); self.propose(view)
                request = next(r for r in self.desk.state['working_memory']['requests'] if r['id'] == view['id'])
                path = self.desk.store.root / 'sanitized' / self.desk.state['workspace'] / (request[field] + '.txt')
                original = path.read_bytes(); path.write_text('[]')
                with self.assertRaises(AddressedMemoryError): self.pilot.selection({'id': view['id'], 'selected': [0]})
                path.write_bytes(original)
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_other_workspace_cannot_use_request_or_source(self):
        view = self.prepare(); other = ContextDesk(self.profile, self.root / 'other-data')
        with self.assertRaises(AddressedMemoryError): AddressedMemoryPilot(other).inputs({'id': view['id']})
        foreign = other.upload({'name': 'foreign.txt', 'data': base64.b64encode(NEW.encode()).decode()})
        with self.assertRaises(ValueError):
            self.pilot.prepare({'version': 1, 'task': 'Проверить источник', 'selected': [foreign]})
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_unicode_whitespace_and_newline_outside_target_are_literal(self):
        before = '\tРоль — контролёр.  \r\nБюджет не подтверждён.\n\nУсловие: после бюджета.  '
        operation = {'action': 'replace', 'old_text': 'Бюджет не подтверждён.', 'new_text': QUOTE, 'quote': QUOTE}
        after, _ = apply_operations(before, [operation], [NEW])
        self.assertEqual(after, '\tРоль — контролёр.  \r\n' + QUOTE + '\n\nУсловие: после бюджета.  ')

    def test_exact_fully_accepted_replay_is_closed_without_model_or_new_version(self):
        view = self.prepare(); self.propose(view)
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        self.pilot.decide({**preview, 'decision': 'accept'})
        replay = self.prepare()
        with patch('gaia.addressed_memory.local_operations') as model:
            result = self.pilot.analyze({'id': replay['id']})
        model.assert_not_called(); self.assertEqual(result['state'], 'done')
        self.assertEqual(self.desk.working_memory()['version'], 2)
        self.assertEqual(len(self.desk.working_memory()['history']), 2)
        self.assertEqual(self.pilot.overview({})['prepared'], [])
        changed = self.pilot.prepare({'version': 2, 'task': 'Другая задача пересмотра.', 'selected': [self.new]})
        with patch('gaia.addressed_memory.local_operations', return_value={'request_id': changed['id'], 'base_version': 2, 'operations': []}) as model:
            self.pilot.analyze({'id': changed['id']})
            deadline = time.monotonic() + 2
            while self.pilot.dispatch('job', {'id': changed['id']})['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
        model.assert_called_once()

    def test_changed_task_or_partially_accepted_batch_can_be_reconsidered(self):
        view = self.prepare()
        self.propose(view, [self.operation(), {'action': 'append', 'old_text': '', 'new_text': 'Дополнительная заметка.', 'quote': QUOTE}])
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        self.pilot.decide({**preview, 'decision': 'accept'})
        for task in ['Обновить статус бюджета.', 'Пересмотреть прежний вывод.']:
            again = self.pilot.prepare({'version': 2, 'task': task, 'selected': [self.new]})
            with patch('gaia.addressed_memory.local_operations', return_value={'request_id': again['id'], 'base_version': 2, 'operations': []}) as model:
                self.pilot.analyze({'id': again['id']})
                deadline = time.monotonic() + 2
                while self.pilot.dispatch('job', {'id': again['id']})['state'] == 'running' and time.monotonic() < deadline:
                    time.sleep(.01)
            model.assert_called_once()

    def test_failed_confirmation_keeps_state_and_can_be_retried(self):
        view = self.prepare(); self.propose(view)
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        before = self.desk.state_path.read_bytes()
        with patch.object(self.desk, '_commit_memory', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(self.desk.state_path.read_bytes(), before)
        self.assertEqual(self.desk.working_memory()['version'], 1)
        self.pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(self.desk.working_memory()['version'], 2)

    def test_human_revision_keeps_address_and_quote_and_invalidates_old_preview(self):
        view = self.prepare(); self.propose(view)
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        proposal = self.pilot.overview({})['proposals'][0]
        revised = self.pilot.revise({'id': view['id'], 'revision': proposal['revision'], 'texts': ['Бюджет подтверждён по новому письму.']})
        change = revised['proposals'][0]['changes'][0]
        self.assertEqual(change['before'], 'Бюджет пока не подтверждён.')
        self.assertEqual(change['quote'], QUOTE)
        self.assertEqual(self.desk.working_memory()['text'], OLD)
        with self.assertRaises(AddressedMemoryError): self.pilot.decide({**preview, 'decision': 'accept'})
        with self.assertRaises(AddressedMemoryError):
            self.pilot.revise({'id': view['id'], 'revision': proposal['revision'], 'texts': ['Другая правка.']})
