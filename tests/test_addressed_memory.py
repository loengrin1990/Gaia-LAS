import base64
import copy
import http.client
import json
import threading
import time
import unittest
from pathlib import Path
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

    def test_cancel_holds_compute_slot_until_worker_exits(self):
        for late_result in ['proposal', 'exception']:
            with self.subTest(late_result=late_result):
                first, second = self.prepare(), self.prepare()
                entered, release = threading.Event(), threading.Event()
                calls, workers = [], []
                original_thread = threading.Thread
                def thread(*args, **kwargs):
                    worker = original_thread(*args, **kwargs); workers.append(worker); return worker
                def model(view):
                    calls.append(view['id'])
                    if view['id'] == first['id']:
                        entered.set()
                        self.assertTrue(release.wait(3), 'test must release the blocked model')
                        if late_result == 'exception': raise RuntimeError('unsafe synthetic detail')
                    return {'request_id': view['id'], 'base_version': view['version'], 'operations': [self.operation()] if view['id'] == first['id'] else []}
                with patch('gaia.addressed_memory.local_operations', side_effect=model), patch('gaia.addressed_memory.threading.Thread', side_effect=thread):
                    try:
                        self.pilot.analyze({'id': first['id']}); self.assertTrue(entered.wait(1))
                        self.pilot.decide({'id': first['id'], 'decision': 'cancel'})
                        with self.assertRaises(AddressedMemoryError): self.pilot.analyze({'id': second['id']})
                        self.assertEqual(calls, [first['id']])
                    finally:
                        release.set()
                        for worker in workers: worker.join(2); self.assertFalse(worker.is_alive())
                    self.pilot.analyze({'id': second['id']}); workers[-1].join(2)
                    self.assertFalse(workers[-1].is_alive())
                self.assertEqual(calls, [first['id'], second['id']])
                self.assertEqual(self.pilot.dispatch('job', {'id': first['id']})['state'], 'cancelled')
                self.assertNotIn('unsafe synthetic detail', self.pilot.dispatch('job', {'id': first['id']})['message'])
                self.assertEqual(self.pilot.dispatch('job', {'id': second['id']})['state'], 'done')
                self.assertEqual(self.desk.working_memory()['text'], OLD)
                self.assertEqual(self.pilot.overview({})['proposals'], [])

    def test_cancel_before_worker_start_and_repeated_cancel_keep_slot_owned(self):
        first, second = self.prepare(), self.prepare()
        targets = []
        class Deferred:
            def __init__(self, target, **_): targets.append(target)
            def start(self): pass
        with patch('gaia.addressed_memory.threading.Thread', Deferred), patch('gaia.addressed_memory.local_operations') as model:
            self.pilot.analyze({'id': first['id']})
            self.pilot.decide({'id': first['id'], 'decision': 'cancel'})
            with self.assertRaises(AddressedMemoryError): self.pilot.decide({'id': first['id'], 'decision': 'cancel'})
            self.assertTrue(self.pilot.overview({})['analysis_busy'])
            self.assertTrue(self.pilot.dispatch('job', {'id': first['id']})['analysis_busy'])
            with self.assertRaises(AddressedMemoryError): self.pilot.analyze({'id': second['id']})
            targets[0](); model.assert_not_called()
            self.assertFalse(self.pilot.overview({})['analysis_busy'])
            model.side_effect = lambda view: {'request_id': view['id'], 'base_version': view['version'], 'operations': []}
            self.pilot.analyze({'id': second['id']}); targets[1]()
            model.assert_called_once()
        self.assertFalse(self.pilot.overview({})['analysis_busy'])
        self.assertEqual(self.pilot.dispatch('job', {'id': first['id']})['state'], 'cancelled')

    def test_worker_terminal_paths_release_compute_slot(self):
        from contextlib import nullcontext
        for outcome in ['model-error', 'proposal-error', 'no-changes', 'proposal']:
            with self.subTest(outcome=outcome):
                pilot = AddressedMemoryPilot(self.desk)
                first, second = self.prepare(), self.prepare()
                workers, original_thread = [], threading.Thread
                def thread(*args, **kwargs):
                    worker = original_thread(*args, **kwargs); workers.append(worker); return worker
                def model(view):
                    if outcome == 'model-error': raise RuntimeError('unsafe synthetic detail')
                    return {'request_id': view['id'], 'base_version': view['version'], 'operations': [self.operation()] if outcome == 'proposal' else []}
                failure = patch.object(pilot, 'propose', side_effect=OSError('unsafe synthetic detail')) if outcome == 'proposal-error' else nullcontext()
                with patch('gaia.addressed_memory.threading.Thread', side_effect=thread), patch('gaia.addressed_memory.local_operations', side_effect=model), failure:
                    pilot.analyze({'id': first['id']}); workers[-1].join(2); self.assertFalse(workers[-1].is_alive())
                self.assertFalse(pilot.overview({})['analysis_busy'])
                status = pilot.dispatch('job', {'id': first['id']})
                self.assertEqual(status['state'], 'failed' if outcome.endswith('error') else 'done')
                self.assertNotIn('unsafe synthetic detail', status['message'])
                with patch('gaia.addressed_memory.threading.Thread', side_effect=thread), patch('gaia.addressed_memory.local_operations', side_effect=lambda view: {'request_id': view['id'], 'base_version': view['version'], 'operations': []}):
                    pilot.analyze({'id': second['id']}); workers[-1].join(2); self.assertFalse(workers[-1].is_alive())
                self.assertFalse(pilot.overview({})['analysis_busy'])
                self.assertEqual(pilot.dispatch('job', {'id': second['id']})['state'], 'done')
                self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_thread_creation_or_start_failure_releases_compute_slot(self):
        for phase in ['create', 'start']:
            with self.subTest(phase=phase):
                first, second = self.prepare(), self.prepare()
                factory = patch('gaia.addressed_memory.threading.Thread')
                with factory as thread:
                    if phase == 'create': thread.side_effect = RuntimeError('unsafe synthetic detail')
                    else: thread.return_value.start.side_effect = RuntimeError('unsafe synthetic detail')
                    with self.assertRaises(AddressedMemoryError): self.pilot.analyze({'id': first['id']})
                self.assertFalse(self.pilot.overview({})['analysis_busy'])
                status = self.pilot.dispatch('job', {'id': first['id']})
                self.assertEqual(status['state'], 'failed'); self.assertNotIn('unsafe synthetic detail', status['message'])
                targets = []
                class Deferred:
                    def __init__(self, target, **_): targets.append(target)
                    def start(self): pass
                with patch('gaia.addressed_memory.threading.Thread', Deferred), patch('gaia.addressed_memory.local_operations', side_effect=lambda view: {'request_id': view['id'], 'base_version': view['version'], 'operations': []}):
                    self.pilot.analyze({'id': second['id']}); targets[0]()
                self.assertFalse(self.pilot.overview({})['analysis_busy'])
                self.assertEqual(self.pilot.dispatch('job', {'id': second['id']})['state'], 'done')

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

    def test_proposal_restores_frozen_task_after_restart(self):
        view = self.prepare(); self.propose(view)
        restarted = AddressedMemoryPilot(ContextDesk(self.profile, self.data))
        self.assertEqual(restarted.overview({})['proposals'][0]['task'], view['task'])
        self.assertEqual(restarted.desk.working_memory()['text'], OLD)

    def test_source_changed_after_preview_cannot_be_confirmed(self):
        row = next(r for r in self.desk.inventory()['items'] if not r['id'].startswith('upload:'))
        self.source.write_text(NEW)
        source = self.desk.preview(row['id'])
        view = self.pilot.prepare({'version': 1, 'task': 'Проверить письмо', 'selected': [source]})
        self.propose(view)
        preview = self.pilot.selection({'id': view['id'], 'selected': [0]})
        self.source.write_text('Письмо отозвано, факт не подтверждён.')
        before = self.desk.state_path.read_bytes()
        with self.assertRaises(ValueError): self.pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(self.desk.state_path.read_bytes(), before)
        self.assertEqual(self.desk.working_memory()['text'], OLD)

    def test_two_desk_ports_keep_distinct_cookies_and_reject_foreign_cookie(self):
        import http.cookiejar
        import urllib.request
        servers = [ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.desk, addressed=True)) for _ in range(2)]
        for server in servers: threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
            bases = [f'http://127.0.0.1:{s.server_port}' for s in servers]
            cookies = []
            for base in bases:
                with opener.open(base) as response:
                    cookies.append(response.headers['Set-Cookie']); response.read()
            self.assertNotEqual(cookies[0].split('=', 1)[0], cookies[1].split('=', 1)[0])
            for cookie in cookies:
                self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
            for base in bases:
                request = urllib.request.Request(base + '/api/desk/addressed/overview', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': base})
                with opener.open(request) as response: self.assertEqual(response.status, 200)
            connection = http.client.HTTPConnection('127.0.0.1', servers[0].server_port)
            for origin, cookie in [(bases[0], cookies[1].split(';', 1)[0]), (bases[1], cookies[0].split(';', 1)[0])]:
                connection.request('POST', '/api/desk/addressed/overview', '{}', {'Content-Type': 'application/json', 'Origin': origin, 'Cookie': cookie})
                response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
            connection.close()
        finally:
            for server in servers: server.shutdown(); server.server_close()

    def test_partial_batch_then_remaining_change_preserves_previously_accepted_facts(self):
        case = next(c for c in json.loads((Path(__file__).parent / 'fixtures/addressed_memory_scenarios.json').read_text()) if c['id'] == 'beacon')
        desk = ContextDesk(self.profile, self.root / 'remainder-cycle')
        upload = lambda text: desk.upload({'name': 'synthetic.txt', 'data': base64.b64encode(text.encode()).decode()})
        seed = desk.prepare_memory({'version': 0, 'task': 'Основа', 'selected': [upload(case['before'])]})
        desk.propose_memory({'id': seed['id'], 'text': case['before']}); desk.decide_memory({'id': seed['id'], 'decision': 'accept'})
        sources = [upload(doc['text']) for doc in case['documents']]
        pilot = AddressedMemoryPilot(desk)
        operations = [dict(action='replace', old_text='Бюджет проекта пока не подтверждён.', new_text='Бюджет проекта подтверждён.', quote='Бюджет проекта подтверждён.'),
                      dict(action='replace', old_text='Приёмка ожидает отчёт о тестировании.', new_text='Приёмка ожидает подписания отчёта о тестировании.', quote='Отчёт о тестировании подготовлен, но ещё не подписан.')]
        for version, batch in [(1, operations), (2, operations[1:])]:
            view = pilot.prepare({'version': version, 'task': case['task'], 'selected': sources})
            before = desk.working_memory()['text']
            pilot.propose({'id': view['id'], 'reply': {'request_id': view['id'], 'base_version': version, 'operations': batch}})
            self.assertEqual(desk.working_memory()['text'], before)
            preview = pilot.selection({'id': view['id'], 'selected': [0]})
            pilot.decide({**preview, 'decision': 'accept'})
        restarted = ContextDesk(self.profile, self.root / 'remainder-cycle')
        expected = case['before']
        for operation in operations: expected = expected.replace(operation['old_text'], operation['new_text'])
        self.assertEqual(restarted.working_memory()['text'], expected)
        self.assertEqual(restarted.working_memory()['version'], 3)
        self.assertEqual(expected.count('Бюджет проекта подтверждён.'), 1)
        self.assertTrue(all(upload(doc['text'])['duplicate'] for doc in case['documents']))

    def test_human_can_exclude_role_event_error_without_losing_supported_proposal(self):
        # Exact quotes cannot by themselves reject a semantic model error.
        old = 'Тестовый запуск назначен на 10 января 2027 года.\nРуководитель проекта подтверждает дату запуска.'
        quote = 'Подрядчик предлагает перенести тестовый запуск на 17 января 2027 года.'
        desk = ContextDesk(self.profile, self.root / 'role-review')
        source = desk.upload({'name': 'synthetic.txt', 'data': base64.b64encode(quote.encode()).decode()})
        seed = desk.prepare_memory({'version': 0, 'task': 'Основа', 'selected': [source]})
        desk.propose_memory({'id': seed['id'], 'text': old}); desk.decide_memory({'id': seed['id'], 'decision': 'accept'})
        pilot = AddressedMemoryPilot(desk)
        view = pilot.prepare({'version': 1, 'task': 'Обновить сведения', 'selected': [source]})
        incorrect = dict(action='replace', old_text='Руководитель проекта подтверждает дату запуска.', new_text='Руководитель проекта не подтверждал перенос.', quote=quote)
        supported = dict(action='append', old_text='', new_text='Предложение подрядчика: перенести запуск на 17 января; решение ещё не принято.', quote=quote)
        pilot.propose({'id': view['id'], 'reply': {'request_id': view['id'], 'base_version': 1, 'operations': [incorrect, supported]}})
        self.assertEqual(desk.working_memory()['text'], old)
        preview = pilot.selection({'id': view['id'], 'selected': [1]})
        self.assertEqual(preview['after'], old + '\n' + supported['new_text'])
        pilot.decide({**preview, 'decision': 'accept'})
        self.assertEqual(desk.working_memory()['text'], preview['after'])
