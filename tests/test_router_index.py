import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from gaia.context_desk import ContextDesk
from gaia.router_index import RouterIndexError, full_text


class RouterIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'memory').mkdir(); (self.root/'router'/'versions').mkdir(parents=True)
        (self.root/'script.py').write_text('')
        (self.root/'profile.json').write_text(json.dumps({'state_dir': str(self.root/'router')}))
        self.profile = {'project': 'Автопретензии', 'memory_root': str(self.root/'memory'), 'router': {
            'script': str(self.root/'script.py'), 'profile': str(self.root/'profile.json'), 'project': 'AP'}}
        self.desk = ContextDesk(self.profile, self.root/'gaia')
        self.index = self.desk.router
        self.events = []
        self.index.call = self.call

    def call(self, command, *args):
        if command == 'scan': return {'schema_version': 1, 'errors': []}
        after = int(args[1])
        # One event per page exercises replay across publication/revocation boundaries.
        batch = [copy.deepcopy(e) for e in self.events if e['cursor'] > after][:1]
        return {'schema_version': 1, 'events': batch, 'next_cursor': batch[-1]['cursor'] if batch else after}

    def event(self, text='Уникальная фраза внутри документа', vid='v1', document='d1', revision=1, status='accepted', project='AP'):
        path = self.root/'router'/'versions'/(vid+'.txt'); path.write_text(text)
        event = dict(cursor=len(self.events)+1, id=vid, document_id=document, revision=revision,
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest(), filename='neutral.txt', snapshot=str(path), status=status, project=project)
        self.events.append(event)
        return event

    def revoke(self, event):
        self.events.append({**event, 'cursor': len(self.events)+1, 'status': 'rejected'})

    def test_content_search_repeat_and_restart(self):
        e = self.event()
        self.assertEqual(self.index.sync()['imported'], 1)
        self.assertEqual(len(self.index.items('фраза внутри')), 1)
        self.assertEqual(self.index.items('несуществующее'), [])
        self.assertEqual(self.index.sync()['imported'], 0)
        restarted = ContextDesk(self.profile, self.root/'gaia')
        self.assertEqual(len(restarted.router.items('фраза')), 1)
        preview = restarted.preview('router:v1')
        packet = restarted.packet({'task': 'Проверить', 'selected': [preview]})
        self.assertIn('Уникальная фраза', packet['text'])
        self.assertIn('Редакция 1', packet['text'])
        self.assertEqual(preview['digest'], e['sha256'])

    def test_new_revision_replaces_and_revoke_never_falls_back(self):
        self.event(text='Старый текст'); self.index.sync()
        old = self.desk.preview('router:v1')
        latest = self.event(text='Новая редакция', vid='v2', revision=2)
        self.index.sync()
        self.assertEqual(set(self.index.active()), {'v2'})
        with self.assertRaises(RouterIndexError):
            self.desk.packet({'task': 'Проверить', 'selected': [old]})
        self.revoke(latest); self.index.sync()
        self.assertEqual(self.index.items(), [])
        self.assertEqual(len(self.index.state['entries']), 2)

    def test_initial_replay_applies_late_revoke_before_import(self):
        first = self.event(); self.revoke(first)
        self.assertEqual(self.index.sync()['imported'], 0)
        self.assertEqual(self.index.items(), [])

    def test_pending_new_candidate_does_not_hide_accepted_old_document(self):
        self.event(); self.event(vid='candidate', document='candidate-doc', status='pending')
        result = self.index.sync()
        self.assertEqual(result['pending'], 1)
        self.assertEqual(set(self.index.active()), {'v1'})

    def test_other_project_never_imported(self):
        self.event(project='HUB'); self.index.sync()
        self.assertEqual(self.index.items(), [])
        self.assertEqual(self.index.state['entries'], {})

    def test_failed_replay_keeps_cursor_and_blocks_stale_search_after_restart(self):
        self.event(); self.index.sync()
        with patch.object(self.index, 'call', side_effect=RouterIndexError('Сбой')):
            with self.assertRaises(RouterIndexError): self.index.sync()
        self.assertEqual(self.index.state['cursor'], 1)
        self.assertEqual(self.index.items(), [])
        restarted = ContextDesk(self.profile, self.root/'gaia')
        self.assertTrue(restarted.router.summary()['blocked'])
        self.index.sync(); self.assertEqual(len(self.index.items()), 1)

    def test_snapshot_integrity_failure_is_visible_not_searchable_and_retry_works(self):
        event = self.event(); path = Path(event['snapshot']); original = path.read_bytes()
        path.write_text('Подмена')
        result = self.index.sync()
        self.assertEqual(result['failed'], 1); self.assertEqual(self.index.items(), [])
        path.write_bytes(original)
        self.assertEqual(self.index.sync()['imported'], 1)

    def test_no_symlinks_or_snapshot_escape(self):
        event = self.event(); outside = self.root/'outside.txt'; outside.write_text('Вне каталога')
        for name in [str(outside), str(self.root/'router'/'versions'/'link.txt')]:
            if name.endswith('link.txt'): Path(name).symlink_to(outside)
            self.events = [{**event, 'snapshot': name}]
            self.assertEqual(self.index.sync()['failed'], 1)
            self.assertEqual(self.index.items(), [])

    def test_saved_text_is_masked_and_tamper_detected(self):
        self.event(text='Контакт person@example.com. Проектная фраза.')
        self.index.sync(); row = self.index.active()['v1']
        self.assertNotIn('person@example.com', self.index.preview('router:v1')['text'])
        path = self.desk.store.root/'sanitized'/self.desk.state['workspace']/(row['artifact']+'.txt')
        path.write_text('Испорчено')
        with self.assertRaises(RouterIndexError): self.index.items()

    def test_latest_failed_revision_does_not_reactivate_old(self):
        self.event(); self.index.sync()
        e = self.event(vid='v2', revision=2); Path(e['snapshot']).write_text('Подмена')
        self.index.sync(); self.assertEqual(self.index.items(), [])

    def test_failed_atomic_commit_does_not_advance_cursor(self):
        self.event()
        from gaia.storage import atomic_write_text
        def failing(path, content):
            if path == self.index.path: raise OSError('disk failure')
            return atomic_write_text(path, content)
        with patch('gaia.router_index.atomic_write_text', side_effect=failing):
            with self.assertRaises(OSError): self.index.sync()
        self.assertEqual(self.index.state['cursor'], 0)
        self.assertEqual(self.index.items(), [])
        self.index.sync(); self.assertEqual(len(self.index.items()), 1)

    def test_full_spreadsheet_reads_beyond_old_preview_limit(self):
        path = self.root/'long.xlsx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="100"><c r="Z100" t="inlineStr"><is><t>Далеко за пределами обзора</t></is></c></row></sheetData></worksheet>')
        text, note = full_text(path, None)
        self.assertIn('Z100: Далеко за пределами обзора', text)
        self.assertIn('не пересчитаны', note)

    def test_changed_profile_cannot_reuse_other_catalog(self):
        self.event(); self.index.sync()
        (self.root/'profile.json').write_text(json.dumps({'state_dir': str(self.root/'other-router')}))
        with self.assertRaises(RouterIndexError): ContextDesk(self.profile, self.root/'gaia')

    def test_catalog_reset_is_blocked(self):
        self.event(); self.index.sync(); self.events = []
        with self.assertRaises(RouterIndexError): self.index.sync()
        self.assertEqual(self.index.items(), [])


if __name__ == '__main__': unittest.main()
