"""Loopback-only human review for closed synthetic bridge sessions. No sender import."""
import argparse
import copy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading

from scripts import tristan_synthetic as bridge

MESSAGES = {
    'preview_changed': 'Предпросмотр устарел. Просмотрите текущий выбор заново.',
    'stale_or_closed_request': 'Проход уже закрыт или версия изменилась. Обновите страницу; повторной записи не будет.',
    'session_busy': 'Сессия занята. Память не изменена. Повторите обзор после завершения текущего действия.',
    'invalid_selection': 'Выберите хотя бы одно изменение для просмотра итога.',
    'incomplete_response': 'Ответ модели не завершён. Изменения не приняты; новый запрос автоматически не запускается.',
    'unresolved_fixture_conflict': 'Этот синтетический кейс содержит известное противоречие и заблокирован. Это не общий детектор смысловых конфликтов.',
    'reply_binding_mismatch': 'Ответ относится к другому пакету или версии. Используйте исходную синтетическую сессию.',
    'invalid_operations_or_literal_evidence': 'Адрес изменения или дословное основание не прошли проверку. Память не изменена.',
    'unauthorized': 'Откройте локальный адрес в этом браузере и обновите страницу.',
}


def envelope(packet, operations, complete=True):
    """Explicit offline demo, never labelled as a model answer."""
    candidate = {k: packet[k] for k in ('request_id', 'base_version', 'packet_digest')}
    candidate['operations'] = operations
    return dict(status='completed' if complete else 'incomplete', output=[dict(
        type='message', role='assistant', status='completed',
        content=[dict(type='output_text', text=json.dumps(candidate, ensure_ascii=False))])])


def demo(kind='partial'):
    if kind == 'conflict':
        return Review(None, 'offline_demo', 'unresolved_fixture_conflict')
    root = bridge.create('unsupported' if kind == 'unsupported' else 'partial')
    with bridge.session(root) as state:
        value = state['packet']
        ops = [] if kind == 'unsupported' else [dict(action='replace', old_text=old, new_text=new, quote=new) for old,new in [
            ('Доступ к тестовой среде ожидается.', 'Доступ к тестовой среде открыт.'),
            ('Инструкция проверки ещё не подготовлена.', 'Инструкция проверки подготовлена.')]]
        response = envelope(value, ops, kind != 'incomplete')
    error = None
    try:
        bridge.ingest(root, response)
    except bridge.BridgeError as exc:
        error = str(exc)
    return Review(root, 'offline_demo', error)


def replay(source):
    """Replay unchanged bound packet/response into a NEW fixture store, never reopen original."""
    with bridge.session(source) as state:
        value, response = copy.deepcopy(state['packet']), copy.deepcopy(state['response'])
        bridge.require(response is not None, 'candidate_required')
        bridge.reply(value, response)
    root = bridge.create(value['case_id'])
    bridge.atomic(root / 'session.json', dict(packet=value, response=response, phase='proposed', version=1,
                  accepted_indices=[], accepted_text=value['before']))
    return Review(root, 'recorded_replay')


class Review:
    def __init__(self, root, mode='existing_session', error=None):
        self.root, self.mode, self.error = root, mode, error
        self.lock = threading.RLock()
        self.ticket = None

    def _view(self, state):
        candidate = bridge.reply(state['packet'], state['response']) if state['response'] else None
        value = dict(phase=state['phase'], version=state['version'], mode=self.mode, error=self.error,
                     before=state['packet']['before'], memory=state['accepted_text'],
                     task=state['packet']['task'], sources=state['packet']['documents'],
                     operations=candidate['operations'] if candidate else [], accepted_indices=state['accepted_indices'])
        value['view_digest'] = bridge.digest(dict(view=value, packet=state['packet'], candidate=candidate))
        return value

    def state(self):
        with self.lock:
            if self.root is None:
                row = bridge.fixture('conflict')
                return dict(phase='blocked', version=1, mode=self.mode, error=self.error, task=row['task'],
                            before=row['before'], memory=row['before'], sources=row['documents'], operations=[],
                            accepted_indices=[], view_digest=None)
            with bridge.session(self.root) as state:
                return self._view(state)

    def action(self, action, body):
        with self.lock:
            bridge.require(self.root is not None, 'unresolved_fixture_conflict')
            keys = {'preview': {'view_digest','indices'}, 'confirm': {'view_digest','indices','preview_token','confirm'},
                    'cancel': {'view_digest'}}
            bridge.require(action in keys and isinstance(body, dict) and set(body) == keys[action], 'invalid_request')
            if action in ('preview','confirm'):
                bridge.require(isinstance(body['indices'],list) and all(type(i) is int for i in body['indices']), 'invalid_selection')
            try:
                if action == 'preview':
                    self.ticket = None
                    with bridge.session(self.root) as state:
                        bridge.active(state)
                        bridge.require(state['phase'] == 'proposed', 'candidate_required')
                        view = self._view(state)
                        bridge.require(body['view_digest'] == view['view_digest'], 'preview_changed')
                        result = bridge.preview(state['packet'], bridge.reply(state['packet'],state['response']), body['indices'])
                    token = secrets.token_urlsafe(24)
                    self.ticket = dict(token=token, view_digest=view['view_digest'], indices=body['indices'], result=result)
                    return dict(preview_token=token, before=result['before'], after=result['after'],
                                indices=body['indices'], selected_count=len(body['indices']))
                view = self.state()
                bridge.require(body['view_digest'] == view['view_digest'], 'preview_changed')
                if action == 'cancel':
                    bridge.cancel(self.root)
                else:
                    ticket = self.ticket
                    bridge.require(body['confirm'] is True and ticket is not None
                        and isinstance(body['preview_token'],str) and secrets.compare_digest(body['preview_token'],ticket['token'])
                        and body['indices'] == ticket['indices'] and body['view_digest'] == ticket['view_digest'], 'preview_changed')
                    bridge.confirm(self.root, ticket['indices'], ticket['result']['preview_digest'], manual=True)
                self.ticket = None
                return self.state()
            except Exception:
                self.ticket = None
                raise


def make_server(review, port=0):
    bridge.require(type(port) is int and port != 8796 and (port == 0 or 1024 <= port <= 65535), 'isolated_port_required')
    token = secrets.token_urlsafe(32)
    static = Path(__file__).parent
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No content, identifiers, filenames or HTTP bodies in logs.

        def _host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def _authorized(self, mutation=False):
            if not self._host(): return False
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if (mutation or origin is not None) and origin != expected: return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site': return False
            try:
                cookie = SimpleCookie(); cookie.load(self.headers.get('Cookie',''))
                value = cookie.get(f'gaia_tristan_review_{self.server.server_port}')
                return value is not None and secrets.compare_digest(value.value, token)
            except (ValueError, TypeError): return False

        def _send(self, status, data, content_type='application/json; charset=utf-8', cookie=False):
            raw = bridge.encode(data) if content_type.startswith('application/json') else data
            self.send_response(status)
            for name,value in [('Content-Type',content_type),('Content-Length',str(len(raw))),('Cache-Control','no-store'),
                ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                ('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")]:
                self.send_header(name,value)
            if cookie:
                self.send_header('Set-Cookie',f'gaia_tristan_review_{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers(); self.wfile.write(raw)

        def _error(self, code, status=409):
            self._send(status, dict(error=MESSAGES.get(code,'Проверка не пройдена. Память не изменена. Обновите обзор или откройте новую синтетическую пробу.')))

        def do_GET(self):
            if not self._host(): self._error('unauthorized',403);return
            routes={'/':('html','text/html; charset=utf-8'),'/review.js':('js','text/javascript; charset=utf-8'),
                    '/review.css':('css','text/css; charset=utf-8')}
            if self.path in routes:
                suffix,kind=routes[self.path]
                self._send(200,(static/('tristan_synthetic_review.'+suffix)).read_bytes(),kind,cookie=self.path=='/');return
            if self.path != '/api/state': self._error('unknown_route',404);return
            if not self._authorized(): self._error('unauthorized',403);return
            try: self._send(200,review.state())
            except (ValueError,OSError,KeyError,TypeError): self._error('invalid_state')

        def do_POST(self):
            if not self._authorized(True): self._error('unauthorized',403);return
            if self.path not in ('/api/preview','/api/confirm','/api/cancel'):self._error('unknown_route',404);return
            if self.headers.get('Content-Type') != 'application/json':self._error('invalid_request',415);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                bridge.require(0<size<=32768 and not self.headers.get('Transfer-Encoding'),'invalid_request')
                body=bridge.parse(self.rfile.read(size))
                self._send(200,review.action(self.path.rsplit('/',1)[1],body))
            except bridge.BridgeError as exc:self._error(str(exc))
            except (ValueError,OSError,KeyError,TypeError):self._error('invalid_request')
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    p=argparse.ArgumentParser(description='Isolated browser review. No network sender or real Gaia access.')
    source=p.add_mutually_exclusive_group()
    source.add_argument('--session',type=Path)
    source.add_argument('--replay-session',type=Path)
    source.add_argument('--demo',choices=['partial','unsupported','incomplete','conflict'],default='partial')
    p.add_argument('--port',type=int,default=0)
    args=p.parse_args()
    try:
        review=Review(args.session) if args.session else replay(args.replay_session) if args.replay_session else demo(args.demo)
        review.state()
        server=make_server(review,args.port)
    except (ValueError,OSError,KeyError,TypeError):
        print('Не удалось открыть изолированную синтетическую пробу. Реальная Gaia не изменена.');raise SystemExit(2)
    print(f'Синтетический review: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
