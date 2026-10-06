"""Synthetic lifecycle tests for the pilot, not a model-quality evaluation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from gaia.context_desk import ContextDesk, DeskError


class WorkingMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        self.source = self.sources / 'letter.txt'
        self.source.write_text('Участник просит подтвердить срок. Ответа нет.')
        self.profile = {'project': 'Тест', 'memory_root': str(self.sources)}
        self.data = self.root / 'private'
        self.desk = ContextDesk(self.profile, self.data)

    def prepare(self):
        row = self.desk.inventory()['items'][0]
        ref = self.desk.preview(row['id'])
        return self.desk.prepare_memory({'version': self.desk.working_memory()['version'],
                                        'task': 'Сопоставить письмо', 'selected': [ref]})['id']

    def propose(self, identifier):
        return self.desk.propose_memory({'id': identifier, 'text': 'Срок не подтверждён. Контакт: test@example.com'})

    def test_accept_is_explicit_and_survives_restart(self):
        identifier = self.prepare()
        draft = self.propose(identifier)
        self.assertEqual(draft['text'], '')
        self.assertNotIn('test@example.com', draft['proposals'][0]['text'])
        accepted = self.desk.decide_memory({'id': identifier, 'decision': 'accept'})
        restarted = ContextDesk(self.profile, self.data)
        self.assertEqual(accepted, restarted.working_memory())
        self.assertEqual(accepted['version'], 1)
        self.assertIn('Срок не подтверждён', accepted['text'])
        self.assertEqual(self.source.read_text(), 'Участник просит подтвердить срок. Ответа нет.')
        self.assertNotIn('Срок не подтверждён', self.desk.state_path.read_text())
        with self.assertRaises(DeskError):
            self.desk.decide_memory({'id': identifier, 'decision': 'accept'})

    def test_rejection_and_repeat_confirmation(self):
        identifier = self.prepare()
        self.propose(identifier)
        self.desk.decide_memory({'id': identifier, 'decision': 'reject'})
        self.assertEqual(self.desk.working_memory()['version'], 0)
        with self.assertRaises(DeskError):
            self.desk.decide_memory({'id': identifier, 'decision': 'accept'})

    def test_changed_source_blocks_proposal_and_acceptance(self):
        identifier = self.prepare()
        self.source.write_text('Изменилось')
        with self.assertRaises(DeskError): self.propose(identifier)
        identifier = self.prepare()
        self.propose(identifier)
        self.source.write_text('Другая редакция')
        with self.assertRaises(DeskError):
            self.desk.decide_memory({'id': identifier, 'decision': 'accept'})
        self.assertEqual(self.desk.working_memory()['text'], '')

    def test_concurrent_draft_cannot_overwrite_accepted_revision(self):
        first, second = self.prepare(), self.prepare()
        self.propose(first); self.propose(second)
        self.desk.decide_memory({'id': first, 'decision': 'accept'})
        with self.assertRaises(DeskError):
            self.desk.decide_memory({'id': second, 'decision': 'accept'})
        self.desk.decide_memory({'id': second, 'decision': 'reject'})
        self.assertEqual(self.desk.working_memory()['version'], 1)

    def test_failed_atomic_write_does_not_accept_in_memory_or_on_disk(self):
        identifier = self.prepare(); self.propose(identifier)
        previous = self.desk.state_path.read_bytes()
        with patch('gaia.context_desk.atomic_write_text', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.desk.decide_memory({'id': identifier, 'decision': 'accept'})
        self.assertEqual(self.desk.working_memory()['version'], 0)
        self.assertEqual(previous, self.desk.state_path.read_bytes())

    def test_foreign_request_and_wrong_version_rejected(self):
        identifier = self.prepare()
        other = ContextDesk(self.profile, self.root / 'other')
        with self.assertRaises(DeskError): other.propose_memory({'id': identifier, 'text': 'Чужое'})
        with self.assertRaises(DeskError): self.desk.prepare_memory({'version': True})
        with self.assertRaises(DeskError): self.desk.prepare_memory({'version': 99})

    def test_prior_memory_is_in_next_request_and_versions_preserved(self):
        first = self.prepare(); self.propose(first)
        self.desk.decide_memory({'id': first, 'decision': 'accept'})
        second = self.prepare()
        self.desk.propose_memory({'id': second, 'text': 'Новая рабочая версия.'})
        self.desk.decide_memory({'id': second, 'decision': 'accept'})
        state = self.desk._memory_state()
        self.assertEqual(len(state['history']), 2)
        self.assertIn('Срок не подтверждён', self.desk._memory_text(state['requests'][1]['previous']))
        self.assertEqual(state['version'], 2)
