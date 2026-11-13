import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from http.server import ThreadingHTTPServer

from gaia.context_desk import ContextDesk, DeskError, extract, make_handler


class ContextDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = self.root / 'memory'
        self.docs = self.root / 'documents'
        self.memory.mkdir(); self.docs.mkdir()
        self.note = self.memory / 'Память_Graph' / '00_Core' / 'Основа.md'
        self.note.parent.mkdir(parents=True)
        self.note.write_text('---\nlast_verified_at: 2026-07-01\n---\nПилот ещё не принят.\nКонтакт: example@example.com')
        self.candidate = self.docs / 'Автопретензии — пилот.txt'
        self.candidate.write_text('Отчёт о пилоте. Требуется проверка.')
        (self.docs / 'Другой проект.txt').write_text('Чужие данные')
        archive = self.memory / 'Память_Graph' / '90_Archive'
        archive.mkdir(); (archive / 'Старое.md').write_text('Архив')
        self.profile = dict(project='Автопретензии', memory_root=str(self.memory), source_roots=[str(self.docs)], terms=['автопретензии'])
        self.data = self.root / 'private'
        self.desk = ContextDesk(self.profile, self.data)

    def preview(self):
        item = next(x for x in self.desk.inventory()['items'] if x['title'] == 'Основа')
        return self.desk.preview(item['id'])

    def test_inventory_is_scoped_and_dates_are_separate(self):
        (self.docs / 'Автопретензии link.txt').symlink_to(self.root / 'outside.txt')
        (self.root / 'outside.txt').write_text('За пределами')
        result = self.desk.inventory()
        self.assertEqual(len(result['items']), 2)
        core = next(x for x in result['items'] if x['memory'])
        self.assertEqual(core['verified_at'], '2026-07-01')
        self.assertTrue(core['basis'])
        self.assertIn('Кандидат', next(x for x in result['items'] if not x['memory'])['kind'])

    def test_preview_masks_and_keeps_original_unchanged(self):
        before = self.note.read_bytes()
        preview = self.preview()
        self.assertNotIn('example@example.com', preview['text'])
        self.assertNotIn('last_verified_at', preview['text'])
        self.assertIn('Пилот ещё не принят', preview['text'])
        self.assertEqual(self.note.read_bytes(), before)
        self.assertEqual(preview['digest'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.desk.state['cards'], [])
        self.assertFalse(list(self.data.glob('tmp*')))

    def test_packet_preserves_grounding_and_rejects_changed_source(self):
        ref = self.preview()
        payload = dict(task='Проверить статус', selected=[ref])
        result = self.desk.packet(payload)
        self.assertIn('Материал 1: Основа', result['text'])
        self.assertIn('2026-07-01', result['text'])
        self.assertIn('не дата актуальности фактов', result['text'])
        self.assertEqual(result['disclosure'], 'local_only')
        self.note.write_text('Новая версия')
        with self.assertRaisesRegex(DeskError, 'изменился'):
            self.desk.packet(payload)

    def test_packet_rejects_duplicates_unknown_and_oversize(self):
        ref = self.preview()
        for selected in ([ref, ref], [dict(id='../../secret', digest='x')], []):
            with self.assertRaises(DeskError):
                self.desk.packet(dict(task='Статус', selected=selected))
        with patch('gaia.context_desk.MAX_PACKET', 10):
            with self.assertRaisesRegex(DeskError, 'не обрезаются'):
                self.desk.packet(dict(task='Статус', selected=[ref]))

    def test_replaced_source_symlink_cannot_escape(self):
        ref = self.preview()
        self.note.unlink()
        outside = self.root / 'outside.txt'; outside.write_text('Не читать')
        self.note.symlink_to(outside)
        with self.assertRaises(DeskError):
            self.desk.preview(ref['id'])

    def test_notes_survive_restart_and_are_reusable_as_drafts(self):
        result = self.desk.save(dict(kind='note', text='Ждём ответ. example@example.com', revision=0))
        self.assertEqual(result['revision'], 1)
        self.assertNotIn('example@example.com', result['cards'][0]['text'])
        with self.assertRaisesRegex(DeskError, 'другом окне'):
            self.desk.save(dict(kind='note', text='Перезапись', revision=0))
        restarted = ContextDesk(self.profile, self.data)
        self.assertEqual(restarted.cards(), result['cards'])
        packet = restarted.packet(dict(task='Продолжить работу', selected=result['cards']))
        self.assertIn('Неподтверждённая рабочая запись', packet['text'])
        self.assertIn('Ждём ответ', packet['text'])
        result = restarted.save(dict(kind='result', text='Предлагаю проверить приёмку', revision=1))
        self.assertEqual(len(result['cards']), 2)
        self.assertEqual(self.note.read_text().count('Предлагаю'), 0)

    def test_project_storage_isolation(self):
        with self.assertRaises(DeskError):
            ContextDesk({**self.profile, 'project': 'Другой'}, self.data)
        with self.assertRaises(DeskError):
            ContextDesk(self.profile, self.docs / 'store')

    def test_gaia_rule_case_flags_preserved(self):
        text = self.desk.clean('иванов иван иванович, КПП 123456789, example@example.com')
        self.assertNotIn('иванов', text)
        self.assertNotIn('123456789', text)
        self.assertNotIn('example@example.com', text)

    def test_docx_extracts_tables_with_explicit_limit_notice(self):
        path = self.docs / 'Тест.docx'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Заголовок</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Ячейка</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        text, note = extract(path)
        self.assertEqual(text, 'Заголовок\nЯчейка')
        self.assertIn('не включены', note)

    def test_http_requires_local_session_and_has_no_export(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.desk))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(conn.close)
        conn.request('POST', '/api/desk/catalog', '{}', {'Content-Type': 'application/json'})
        response = conn.getresponse(); response.read(); self.assertEqual(response.status, 403)
        for action in ('memory', 'memory/prepare', 'memory/propose', 'memory/decide', 'memory/export-preview', 'memory/authorize-copy'):
            conn.request('POST', '/api/desk/' + action, '{}', {'Content-Type': 'application/json'})
            response = conn.getresponse(); response.read(); self.assertEqual(response.status, 403)
        conn.request('GET', '/')
        response = conn.getresponse(); html = response.read().decode()
        cookie = response.getheader('Set-Cookie').split(';')[0]
        self.assertIn('Только на этом компьютере', html)
        headers = {'Cookie': cookie, 'Origin': f'http://127.0.0.1:{server.server_port}', 'Content-Type': 'application/json'}
        conn.request('POST', '/api/desk/catalog', '{}', headers)
        response = conn.getresponse(); data = json.loads(response.read())
        self.assertEqual(response.status, 200); self.assertEqual(data['project'], 'Автопретензии')
        conn.request('POST', '/api/desk/memory', '{}', headers)
        response = conn.getresponse(); memory = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(memory['version'], 0)
        self.assertEqual(memory['disclosure'], 'local_only')
        conn.request('POST', '/api/desk/memory/prepare', json.dumps({
            'version': 0, 'task': 'Уточнить статус', 'selected': [self.preview()]}), headers)
        response = conn.getresponse(); prepared = json.loads(response.read())
        self.assertEqual(response.status, 200)
        conn.request('POST', '/api/desk/memory/propose', json.dumps({
            'id': prepared['id'], 'text': 'Пилот ещё не принят.'}), headers)
        response = conn.getresponse(); proposed = json.loads(response.read())
        self.assertEqual(response.status, 200); self.assertEqual(proposed['text'], '')
        conn.request('POST', '/api/desk/memory/decide', json.dumps({
            'id': prepared['id'], 'decision': 'accept'}), headers)
        response = conn.getresponse(); accepted = json.loads(response.read())
        self.assertEqual(response.status, 200); self.assertEqual(accepted['version'], 1)
        conn.request('POST', '/api/desk/export', '{}', headers)
        response = conn.getresponse(); response.read(); self.assertEqual(response.status, 404)
        conn.request('POST', '/api/desk/catalog', '{}', {**headers, 'Origin': 'https://outside.example'})
        response = conn.getresponse(); response.read(); self.assertEqual(response.status, 403)
        conn.request('GET', '/', headers={'Host': 'outside.example'})
        response = conn.getresponse(); response.read(); self.assertEqual(response.status, 403)


if __name__ == '__main__':
    unittest.main()
