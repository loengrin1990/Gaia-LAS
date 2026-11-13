"""Development-only, closed-fixture bridge. No Gaia runtime/provider integration.

Run from the checkout: python3 -B -m scripts.tristan_synthetic --help
Only tristan_synthetic_send's explicit paid action can perform network I/O.
"""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import unicodedata

from gaia.addressed_memory import apply_operations

CORPUS = Path(__file__).resolve().parents[1] / 'tests/fixtures/addressed_memory_holdout.json'
CORPUS_SHA = 'eff79c11f173f1380e002df6bf478aaf13ffc51072e17e53da228ff92e55b863'
CASES = ('role', 'event', 'proposal', 'conflict', 'unsupported', 'partial')
PREFIX = 'gaia-tristan-synthetic-'
MAX_BYTES = 1_000_000


class BridgeError(ValueError):
    """Content-free failure: don't include response bodies, paths or credentials."""


def require(condition, code):
    if not condition:
        raise BridgeError(code)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def parse(raw):
    require(len(raw) <= MAX_BYTES, 'response_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(BridgeError('invalid_json')))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError('invalid_json') from None


def fixture(case_id):
    raw = CORPUS.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == CORPUS_SHA, 'corpus_changed')
    require(case_id in CASES, 'synthetic_case_required')
    rows = parse(raw)
    require(tuple(row['id'] for row in rows) == CASES, 'corpus_changed')
    return next(row for row in rows if row['id'] == case_id)


def packet(case_id, request_id):
    require(isinstance(request_id, str) and re.fullmatch('[0-9a-f]{32}', request_id), 'invalid_request_id')
    row = fixture(case_id)
    value = dict(protocol='gaia-tristan-synthetic-v1', case_id=case_id, corpus_sha256=CORPUS_SHA,
                 request_id=request_id, base_version=1, before=row['before'],
                 documents=row['documents'], task=row['task'])
    value['packet_digest'] = digest(value)
    return value


def validate_packet(value):
    require(isinstance(value, dict), 'invalid_packet')
    expected = packet(value.get('case_id'), value.get('request_id'))
    require(encode(value) == encode(expected), 'packet_changed')
    require(value['case_id'] != 'conflict', 'unresolved_fixture_conflict')
    require(len(encode(value)) <= 16_000, 'packet_too_large')
    return value


def reply(value, response):
    """Strict completed Responses API envelope; no reasoning/tool/refusal as answer."""
    validate_packet(value)
    require(isinstance(response, dict) and response.get('status') == 'completed'
            and not response.get('error') and not response.get('incomplete_details'), 'incomplete_response')
    output = response.get('output')
    require(isinstance(output, list), 'missing_final_answer')
    messages = []
    for item in output:
        require(isinstance(item, dict), 'invalid_output')
        if item.get('type') == 'reasoning':
            continue
        require(item.get('type') == 'message' and item.get('role') == 'assistant'
                and item.get('status') == 'completed', 'unexpected_output')
        messages.append(item)
    require(len(messages) == 1, 'missing_final_answer')
    content = messages[0].get('content')
    require(isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)
            and content[0].get('type') == 'output_text'
            and isinstance(content[0].get('text'), str), 'missing_final_answer')
    candidate = parse(content[0]['text'].encode())
    require(isinstance(candidate, dict) and set(candidate) ==
            {'request_id', 'base_version', 'packet_digest', 'operations'}, 'invalid_reply_schema')
    require(candidate['request_id'] == value['request_id']
            and type(candidate['base_version']) is int and candidate['base_version'] == value['base_version']
            and candidate['packet_digest'] == value['packet_digest'], 'reply_binding_mismatch')
    try:
        apply_operations(value['before'], candidate['operations'], [d['text'] for d in value['documents']])
    except ValueError:
        raise BridgeError('invalid_operations_or_literal_evidence') from None
    return candidate


def preview(value, candidate, indices):
    validate_packet(value)
    require(isinstance(indices, list) and indices and all(type(i) is int for i in indices)
            and len(set(indices)) == len(indices)
            and all(0 <= i < len(candidate['operations']) for i in indices), 'invalid_selection')
    selected = [candidate['operations'][i] for i in indices]
    try:
        after, cards = apply_operations(value['before'], selected, [d['text'] for d in value['documents']])
    except ValueError:
        raise BridgeError('invalid_operations_or_literal_evidence') from None
    result = dict(packet_digest=value['packet_digest'], candidate_digest=digest(candidate),
                  indices=indices, before=value['before'], after=after, changes=cards,
                  sources=value['documents'])
    result['preview_digest'] = digest(result)
    return result


def atomic(path, value):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(encode(value)); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def create(case_id):
    value = packet(case_id, uuid.uuid4().hex)
    validate_packet(value)  # Conflicts never become sendable requests.
    root = Path(tempfile.mkdtemp(prefix=PREFIX))
    atomic(root / 'session.json', dict(packet=value, version=1, phase='prepared', response=None,
                                     accepted_indices=[], accepted_text=value['before']))
    return root


@contextlib.contextmanager
def session(root):
    root = Path(root)
    require(root.is_absolute() and not root.is_symlink() and root.parent.resolve() == Path(tempfile.gettempdir()).resolve()
            and root.name.startswith(PREFIX) and root.is_dir(), 'isolated_session_required')
    require(not (root / 'lock').is_symlink() and not (root / 'session.json').is_symlink(), 'invalid_session_files')
    with (root / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError('session_busy') from None
        state = parse((root / 'session.json').read_bytes())
        validate_packet(state['packet'])
        require(state['phase'] in ('prepared', 'proposed', 'confirmed', 'cancelled'), 'invalid_state')
        expected = state['packet']['before']
        if state['response'] is not None:
            candidate = reply(state['packet'], state['response'])
        if state['phase'] == 'confirmed':
            require(state['response'] is not None and type(state['version']) is int and state['version'] == 2, 'invalid_state')
            expected = preview(state['packet'], candidate, state['accepted_indices'])['after']
        else:
            require(type(state['version']) is int and state['version'] == 1 and state['accepted_indices'] == [], 'stale_version')
        require(state['accepted_text'] == expected, 'accepted_memory_changed')
        yield state


def active(state):
    require(state['version'] == state['packet']['base_version'] and state['phase'] in ('prepared', 'proposed'),
            'stale_or_closed_request')


def ingest(root, response):
    with session(root) as state:
        active(state)
        require(state['phase'] == 'prepared', 'reply_already_staged')
        candidate = reply(state['packet'], response)
        state.update(response=response, phase='proposed')
        atomic(Path(root) / 'session.json', state)
        return candidate


def review(root, indices):
    with session(root) as state:
        active(state)
        require(state['phase'] == 'proposed', 'candidate_required')
        candidate = reply(state['packet'], state['response'])
        return preview(state['packet'], candidate, indices)


def confirm(root, indices, preview_digest, manual=False):
    with session(root) as state:
        active(state)
        require(manual is True and state['phase'] == 'proposed', 'explicit_confirmation_required')
        current = preview(state['packet'], reply(state['packet'], state['response']), indices)
        require(current['preview_digest'] == preview_digest, 'preview_changed')
        state.update(version=2, phase='confirmed', accepted_indices=indices, accepted_text=current['after'])
        atomic(Path(root) / 'session.json', state)
        return state['accepted_text']


def cancel(root):
    with session(root) as state:
        require(state['phase'] != 'confirmed', 'already_confirmed')
        state['phase'] = 'cancelled'
        atomic(Path(root) / 'session.json', state)


def human_review(result):
    """Plain text review avoids JSON-escaped paragraphs and hides reasoning traces."""
    if 'preview_digest' in result:
        lines = ['ПРЕДПРОСМОТР — память ещё не сохранена',
                 'Выбрано операций: ' + str(len(result['indices'])),
                 'ДО:', result['before'], 'ПОСЛЕ:', result['after'],
                 'Digest preview: ' + result['preview_digest']]
        cards = result['changes']
    else:
        value = result['packet']
        lines = ['ТОЛЬКО СИНТЕТИЧЕСКАЯ ПРОБА', 'Состояние: ' + result['phase'],
                 'Принятая версия: ' + str(result['version']),
                 'Request ID: ' + value['request_id'], 'Digest пакета: ' + value['packet_digest'],
                 'ЗАДАЧА:', value['task'], 'ПРИНЯТАЯ ПАМЯТЬ:', result['accepted_text'], result['review_notice']]
        cards = result['candidate']['operations'] if result['candidate'] else []
    for offset, card in enumerate(cards):
        index = result['indices'][offset] if 'preview_digest' in result else offset
        lines += ['ОПЕРАЦИЯ ' + str(index), 'Было: ' + card.get('old_text',card.get('before','')),
                  'Предложено: ' + card.get('new_text',card.get('after','')), 'Цитата: ' + card['quote']]
    sources = result.get('sources', result.get('packet', {}).get('documents', []))
    for source in sources:
        lines += ['ПОЛНЫЙ ИСТОЧНИК: ' + source['name'], source['text']]
    lines += ['Проверьте смысл каждой выбранной операции; буквальная цитата не доказывает вывод.']
    if result.get('phase') in ('confirmed', 'cancelled'):
        lines += ['Проход закрыт. Принятые индексы: ' + str(result.get('accepted_indices', [])),
                  'Показано исходное предложение; непринятые операции не сохранены. Повторный confirm запрещён.']
    else:
        lines += ['Следующий шаг: preview с индексами (с 0), затем отдельный confirm с digest. Отказ: cancel.']
    # Do not allow even a synthetic model response to inject terminal control sequences.
    return ''.join('\\u%04x' % ord(c) if c not in '\n\t' and unicodedata.category(c) in {'Cc', 'Cf'} else c
                   for c in '\n\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description='Synthetic-only review. No live Gaia store. No network from this command.')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('create').add_argument('case', choices=CASES)
    for action in ('show', 'import', 'preview', 'confirm', 'cancel'):
        p = sub.add_parser(action); p.add_argument('session', type=Path)
        if action in ('show', 'preview'): p.add_argument('--json', action='store_true')
        if action == 'import': p.add_argument('response', type=Path)
        if action in ('preview', 'confirm'): p.add_argument('--indices', required=True, type=int, nargs='+')
        if action == 'confirm':
            p.add_argument('--preview-digest', required=True)
            p.add_argument('--confirm-synthetic-memory', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'create': print(create(args.case)); return
        if args.action == 'show':
            with session(args.session) as state:
                result = dict(phase=state['phase'], version=state['version'], packet=state['packet'],
                              accepted_text=state['accepted_text'], accepted_indices=state['accepted_indices'],
                              candidate=reply(state['packet'],state['response']) if state['response'] else None,
                              review_notice='Буквальная цитата не доказывает вывод. Проверьте обязанности, условия, планы и противоречия. '
                              'Все источники ниже приведены полностью. Индексы операций начинаются с 0. '
                              'Нет операций — не доказательство полноты. Preview не сохраняет память.')
        elif args.action == 'import': result = ingest(args.session, parse(args.response.read_bytes()))
        elif args.action == 'preview': result = review(args.session, args.indices)
        elif args.action == 'confirm': result = confirm(args.session, args.indices, args.preview_digest, args.confirm_synthetic_memory)
        else: cancel(args.session); result = 'cancelled; accepted memory unchanged'
        if args.action in ('show','preview') and not args.json: print(human_review(result))
        else: print(json.dumps(result, ensure_ascii=False, indent=2))
    except (BridgeError, OSError, KeyError, TypeError) as error:
        # BridgeError messages are constant safe codes, never derived from response text.
        code = str(error) if isinstance(error, BridgeError) else 'invalid_or_unavailable_session'
        print('Операция отклонена: ' + code + '. Принятая память не изменена.')
        raise SystemExit(2)


if __name__ == '__main__':
    main()
