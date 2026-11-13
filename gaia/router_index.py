"""Local, fail-closed consumer of the standalone router's version journal."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from .masking import GAIA_RULES
from .protection import protect
from .storage import atomic_write_text


class RouterIndexError(ValueError):
    pass


def full_text(path, extractor):
    if path.suffix.lower() != '.xlsx':
        return extractor(path)
    # Unlike the old preview, read every stored cell, with an explicit hard bound.
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 40_000_000:
            raise RouterIndexError('Таблица слишком велика после распаковки.')
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        parts = []
        for name in sorted(n for n in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)):
            root = ET.fromstring(archive.read(name))
            parts.append('Лист ' + Path(name).stem.removeprefix('sheet'))
            for row in root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'):
                cells = []
                for cell in row:
                    value = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                    inline = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is')
                    text = value.text or '' if value is not None else ''
                    if cell.get('t') == 's' and text:
                        text = shared[int(text)]
                    if inline is not None:
                        text = ''.join(inline.itertext())
                    cells.append(cell.get('r', '') + ': ' + text)
                parts.append(' | '.join(cells))
        text = '\n'.join(parts)
        if len(text) > 200_000 or not text.strip():
            raise RouterIndexError('Таблица пуста или превышает предел текста.')
        return text, 'Прочитаны сохранённые значения всех ячеек. Формулы не пересчитаны, рисунки не извлечены.'


class RouterIndex:
    def __init__(self, desk, settings):
        self.desk = desk
        self.settings = settings
        self.script = Path(settings['script']).resolve(strict=True)
        self.profile = Path(settings['profile']).resolve(strict=True)
        self.project = settings['project']
        config = json.loads(self.profile.read_text())
        self.snapshots = (Path(config['state_dir']) / 'versions').resolve()
        self.path = desk.data_dir / 'metadata' / 'router_index.json'
        self.block = desk.data_dir / 'metadata' / 'router_sync_blocked'
        self.identity = hashlib.sha256(json.dumps([str(self.script), config, self.project], sort_keys=True).encode()).hexdigest()
        self.state = {'identity': self.identity, 'cursor': 0, 'entries': {}, 'synced_at': ''}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state.get('identity') != self.identity:
                raise RouterIndexError('Настройки роутера изменились. Используйте отдельное рабочее хранилище.')

    def call(self, command, *args):
        result = subprocess.run([self.settings.get('python', 'python3'), '-B', str(self.script),
                                 '--profile', str(self.profile), '--json', command, *map(str, args)],
                                capture_output=True, timeout=300, cwd=str(self.script.parent))
        try:
            data = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise RouterIndexError('Роутер не вернул читаемый результат.') from None
        if data.get('schema_version') != 1 or data.get('error') or result.returncode not in (0, 2):
            raise RouterIndexError('Обновление роутера остановлено. Проверьте его каталог и снимки документов.')
        if result.returncode and not (command == 'scan' and isinstance(data.get('errors'), list)):
            raise RouterIndexError('Роутер не завершил операцию.')
        return data

    def _bytes(self, event):
        path = Path(event['snapshot'])
        if not path.is_absolute() or not path.is_relative_to(self.snapshots):
            raise RouterIndexError('Снимок находится вне хранилища роутера.')
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise RouterIndexError('Снимок не может быть ссылкой.')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RouterIndexError('Снимок не является обычным файлом.')
            data = stream.read(20_000_001)
        if len(data) > 20_000_000:
            raise RouterIndexError('Документ превышает ограничение Gaia: 20 МБ.')
        if hashlib.sha256(data).hexdigest() != event['sha256']:
            raise RouterIndexError('Контрольная сумма снимка не совпадает.')
        return data

    def _import(self, event):
        from .context_desk import extract
        data = self._bytes(event)
        with tempfile.TemporaryDirectory(dir=self.desk.data_dir) as folder:
            path = Path(folder) / ('document' + Path(event['filename']).suffix.lower())
            path.write_bytes(data)
            text, note = full_text(path, extract)
        store, ws = self.desk.store, self.desk.state['workspace']
        source = store.accept_bytes(ws, data, 'application/octet-stream')
        extraction = store._versioned_content(ws, 'extraction', 'art', text, source['source_id'], 'router-index-v1', '')
        rules = [(r.category, '(?i:' + r.pattern.pattern + ')' if r.pattern.flags & re.I else r.pattern.pattern, True) for r in GAIA_RULES]
        sanitized = protect(store, ws, extraction['artifact_id'], extra_rules=rules)['sanitized']
        return {'source': source['source_id'], 'artifact': sanitized['artifact_id'], 'checksum': sanitized['checksum'], 'note': note}

    def sync(self):
        from .context_desk import stamp
        # Marker persists across a crash; partially replayed data is never published.
        atomic_write_text(self.block, 'updating')
        scan = self.call('scan')
        cursor, events = 0, {}
        for _ in range(1000):
            page = self.call('changes', '--after', cursor, '--limit', 100)
            batch = page.get('events')
            if not isinstance(batch, list):
                raise RouterIndexError('Журнал изменений повреждён.')
            if not batch:
                if page.get('next_cursor') != cursor:
                    raise RouterIndexError('Нарушена последовательность журнала.')
                break
            for event in batch:
                if not isinstance(event.get('cursor'), int) or event['cursor'] <= cursor or event.get('status') not in {'accepted', 'pending', 'rejected'}:
                    raise RouterIndexError('Нарушена последовательность журнала.')
                cursor = event['cursor']
                events[event['id']] = event
            if page.get('next_cursor') != cursor:
                raise RouterIndexError('Нарушена последовательность журнала.')
        else:
            raise RouterIndexError('Журнал слишком велик для одного обновления.')
        if cursor < self.state['cursor']:
            raise RouterIndexError('Каталог роутера был заменён или восстановлен. Нужна проверка истории.')
        entries, imported = {}, 0
        for vid, event in events.items():
            if event['project'] != self.project and not (event['project'] == 'UNKNOWN' and event['status'] == 'pending'):
                continue
            old = self.state['entries'].get(vid, {})
            if old and old['sha256'] != event['sha256']:
                raise RouterIndexError('Изменилось содержимое уже зарегистрированной версии.')
            row = {k: event[k] for k in ('id', 'document_id', 'revision', 'sha256', 'filename', 'status')}
            if event['status'] == 'accepted':
                try:
                    # Revalidate even when reusing previously indexed text.
                    self._bytes(event)
                    if old.get('artifact'):
                        row.update({k: old[k] for k in ('artifact', 'source', 'checksum', 'note')})
                        try:
                            self._text(row)
                        except (OSError, RouterIndexError):
                            row.update(self._import(event)); imported += 1
                    else:
                        row.update(self._import(event)); imported += 1
                except Exception:
                    row['problem'] = 'Не удалось прочитать или проиндексировать эту версию. Проверьте формат, размер и целостность; повторите обновление.'
            elif old.get('artifact'):
                row.update({k: old[k] for k in ('artifact', 'source', 'checksum', 'note')})
            entries[vid] = row
        state = {'identity': self.identity, 'cursor': cursor, 'entries': entries, 'synced_at': stamp()}
        atomic_write_text(self.path, json.dumps(state, ensure_ascii=False))
        self.state = state
        self.block.unlink(missing_ok=True)
        return {**self.summary(), 'imported': imported, 'scan_errors': len(scan.get('errors', []))}

    def _text(self, row):
        path = self.desk.store.root / 'sanitized' / self.desk.state['workspace'] / (row['artifact'] + '.txt')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['checksum']:
            raise RouterIndexError('Индекс повреждён. Повторите обновление документов.')
        return data.decode('utf-8')

    def active(self):
        if self.block.exists():
            return {}
        heads = {}
        for row in self.state['entries'].values():
            # Rejected latest revision blocks fallback to an older revision.
            current = heads.get(row['document_id'])
            if not current or row['revision'] > current['revision']:
                heads[row['document_id']] = row
        return {r['id']: r for r in heads.values() if r['status'] == 'accepted' and r.get('artifact') and not r.get('problem')}

    def summary(self):
        rows = list(self.state['entries'].values())
        return {'enabled': True, 'blocked': self.block.exists(), 'active': len(self.active()),
                'pending': sum(r['status'] == 'pending' for r in rows),
                'excluded': sum(r['status'] == 'rejected' for r in rows),
                'failed': sum(bool(r.get('problem')) for r in rows),
                'problems': [{'title': r['filename'], 'message': r['problem']} for r in rows if r.get('problem')],
                'review_titles': [r['filename'] for r in rows if r['status'] == 'pending'], 'synced_at': self.state['synced_at']}

    def items(self, query=''):
        from .context_desk import normalized
        terms = normalized(query).split()
        rows = []
        for vid, row in self.active().items():
            text = self._text(row)
            haystack = normalized(row['filename'] + '\n' + text)
            if terms and not all(term in haystack for term in terms):
                continue
            rows.append({'id': 'router:' + vid, 'title': row['filename'], 'memory': False,
                         'kind': 'Документ из каталога', 'origin': 'Редакция ' + str(row['revision']),
                         'format': Path(row['filename']).suffix[1:].upper(), 'verified_at': '', 'modified_at': '',
                         'basis': False, 'indexed': True, 'revision': row['revision']})
        return rows

    def preview(self, identifier):
        row = self.active().get(identifier.removeprefix('router:'))
        if not row:
            raise RouterIndexError('Версия исключена, заменена или недоступна. Обновите подборку.')
        text = self._text(row)
        return {'id': identifier, 'title': row['filename'], 'digest': row['sha256'], 'text': text,
                'note': 'Редакция ' + str(row['revision']) + '. ' + row['note'] + ' Принадлежность проекту подтверждена; актуальность утверждений требует проверки.',
                'chars': len(text), 'disclosure': 'local_only'}
