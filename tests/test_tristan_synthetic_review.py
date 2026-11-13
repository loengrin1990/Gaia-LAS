"""Offline safety/HTTP tests for isolated synthetic review."""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest

from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_review as ui


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.review=ui.demo();self.root=self.review.root;self.addCleanup(shutil.rmtree,self.root)
        self.initial=(self.root/'session.json').read_bytes()
    def select(self,indices=None):
        return self.review.action('preview',dict(view_digest=self.review.state()['view_digest'],indices=[0] if indices is None else indices))
    def confirm_body(self,preview):
        return dict(view_digest=self.review.state()['view_digest'],indices=preview['indices'],preview_token=preview['preview_token'],confirm=True)

    def test_view_and_preview_do_not_accept_or_write(self):
        view=self.review.state();preview=self.select()
        self.assertEqual((self.root/'session.json').read_bytes(),self.initial)
        self.assertEqual(view['version'],1);self.assertEqual(preview['selected_count'],1)
        self.assertEqual(preview['after'],view['before'].replace('Доступ к тестовой среде ожидается.','Доступ к тестовой среде открыт.'))
        self.assertNotIn('response',view);self.assertNotIn('reasoning',json.dumps(view))
        self.assertEqual(view['sources'],b.fixture('partial')['documents'])

    def test_explicit_confirm_partial_restart_duplicate(self):
        preview=self.select();body=self.confirm_body(preview);state=self.review.action('confirm',body)
        self.assertEqual(state['phase'],'confirmed');self.assertEqual(state['version'],2)
        self.assertEqual(state['accepted_indices'],[0])
        self.assertIn('Инструкция проверки ещё не подготовлена.',state['memory'])
        self.assertIn('Работа разрешена после проверки пользовательских прав.',state['memory'])
        self.assertEqual(ui.Review(self.root).state()['memory'],state['memory'])
        with self.assertRaises(b.BridgeError):self.review.action('confirm',body)
        self.assertEqual(self.review.state()['version'],2)

    def test_no_preview_no_ack_or_wrong_selection_is_refused(self):
        body=dict(view_digest=self.review.state()['view_digest'],indices=[0],preview_token='guess',confirm=True)
        with self.assertRaises(b.BridgeError):self.review.action('confirm',body)
        for key,value in [('confirm',False),('indices',[1]),('indices',[False]),('preview_token','guess')]:
            preview=self.select();body=self.confirm_body(preview);body[key]=value
            with self.subTest(key=key),self.assertRaises(b.BridgeError):self.review.action('confirm',body)
        self.assertEqual((self.root/'session.json').read_bytes(),self.initial)

    def test_old_preview_is_invalid_after_new_preview(self):
        old=self.select();new=self.select([1])
        with self.assertRaises(b.BridgeError):self.review.action('confirm',self.confirm_body(old))
        # A failed acceptance invalidates outstanding tickets; obtain a new preview.
        latest=self.select([1]);state=self.review.action('confirm',self.confirm_body(latest))
        self.assertEqual(state['accepted_indices'],[1])

    def test_changed_candidate_invalidates_view_and_preview(self):
        old=self.select();body=self.confirm_body(old)
        data=b.parse(self.initial);answer=json.loads(data['response']['output'][0]['content'][0]['text'])
        answer['operations'][0]['new_text']='Доступ открыт; требуется отдельная проверка прав.'
        data['response']['output'][0]['content'][0]['text']=json.dumps(answer,ensure_ascii=False)
        b.atomic(self.root/'session.json',data)
        with self.assertRaisesRegex(b.BridgeError,'preview_changed'):self.review.action('confirm',body)
        self.assertEqual(self.review.state()['version'],1)

    def test_packet_source_or_version_change_fails_closed(self):
        for key in ['source','version']:
            data=b.parse(self.initial)
            if key=='source':data['packet']['documents'][0]['text']+=' altered'
            else:data['version']=2
            b.atomic(self.root/'session.json',data)
            with self.subTest(key=key),self.assertRaises(b.BridgeError):self.review.state()
        b.atomic(self.root/'session.json',b.parse(self.initial))

    def test_cancel_with_preview_no_write_and_late_confirm(self):
        preview=self.select();body=self.confirm_body(preview)
        self.review.action('cancel',dict(view_digest=body['view_digest']))
        self.assertEqual(self.review.state()['memory'],b.fixture('partial')['before'])
        self.assertEqual(self.review.state()['version'],1)
        with self.assertRaises(b.BridgeError):self.review.action('confirm',body)
        self.review.action('cancel',dict(view_digest=self.review.state()['view_digest']))
        self.assertEqual(self.review.state()['phase'],'cancelled')

    def test_empty_incomplete_and_known_conflict_are_explicit(self):
        for kind in ['unsupported','incomplete','conflict']:
            review=ui.demo(kind)
            if review.root:self.addCleanup(shutil.rmtree,review.root)
            state=review.state();self.assertEqual(state['operations'],[]);self.assertEqual(state['version'],1)
            with self.assertRaises(b.BridgeError):review.action('preview',dict(view_digest=state['view_digest'],indices=[0]))
            if kind=='incomplete':self.assertEqual(state['error'],'incomplete_response')
            if kind=='conflict':self.assertEqual(state['phase'],'blocked');self.assertIn('не общий',ui.MESSAGES[state['error']])

    def test_replay_preserves_binding_and_original_store(self):
        selected=self.select();self.review.action('confirm',self.confirm_body(selected))
        original=(self.root/'session.json').read_bytes()
        replay=ui.replay(self.root);self.addCleanup(shutil.rmtree,replay.root)
        self.assertNotEqual(replay.root,self.root);self.assertEqual(replay.state()['version'],1)
        self.assertEqual(replay.state()['mode'],'recorded_replay')
        with b.session(replay.root) as target,b.session(self.root) as source:
            self.assertEqual(target['packet'],source['packet']);self.assertEqual(target['response'],source['response'])
        self.assertEqual(original,(self.root/'session.json').read_bytes())

    def test_bad_schema_and_arbitrary_store_fields_are_refused(self):
        body=dict(view_digest=self.review.state()['view_digest'],indices=[0],store_path='/not-a-store')
        with self.assertRaises(b.BridgeError):self.review.action('preview',body)
        self.assertEqual((self.root/'session.json').read_bytes(),self.initial)

    def test_live_port_is_rejected_without_binding(self):
        with self.assertRaises(b.BridgeError):ui.make_server(self.review,8796)


class ReviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.review=ui.demo();self.addCleanup(shutil.rmtree,self.review.root)
        self.server=ui.make_server(self.review);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close);self.port=self.server.server_port;self.host=f'127.0.0.1:{self.port}'
        _,headers,_=self.request('GET','/');self.cookie=dict(headers)['Set-Cookie'].split(';',1)[0]
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self,method,path,body=None,headers=None):
        values={'Host':self.host}
        if hasattr(self,'cookie'):values['Cookie']=self.cookie
        if method=='POST':values.update(Origin='http://'+self.host,**{'Content-Type':'application/json'})
        if headers:values.update(headers)
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            connection.request(method,path,b.encode(body) if body is not None else None,values)
            response=connection.getresponse();raw=response.read();return response.status,response.getheaders(),raw
        finally:connection.close()

    def test_security_cookie_csp_no_store_and_auth(self):
        status,headers,_=self.request('GET','/');headers=dict(headers)
        self.assertEqual(status,200);self.assertIn('HttpOnly; SameSite=Strict',headers['Set-Cookie'])
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy']);self.assertEqual(headers['Cache-Control'],'no-store')
        for headers in [{'Cookie':''},{'Host':'attacker.invalid'},{'Origin':'https://attacker.invalid'},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request('GET','/api/state',headers=headers)[0],403)
        self.assertEqual(self.request('POST','/api/cancel',{},headers={'Origin':'https://attacker.invalid'})[0],403)

    def test_real_http_preview_confirm_cycle_requires_separate_confirmation(self):
        status,_,raw=self.request('GET','/api/state');state=json.loads(raw);self.assertEqual(status,200)
        self.assertEqual(self.review.state()['version'],1)
        status,_,raw=self.request('POST','/api/preview',dict(view_digest=state['view_digest'],indices=[0]));self.assertEqual(status,200);preview=json.loads(raw)
        self.assertEqual(self.review.state()['version'],1)
        payload=dict(view_digest=state['view_digest'],indices=[0],preview_token=preview['preview_token'],confirm=True)
        status,_,raw=self.request('POST','/api/confirm',payload);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['version'],2)
        self.assertEqual(self.request('POST','/api/confirm',payload)[0],409)

    def test_no_send_upload_config_files_or_general_provider_routes(self):
        for path in ['/api/send','/api/upload','/config.json','/../../session.json','/api/providers','/workspace','/workspace.js','/api/workspace','/api/prepare']:
            self.assertEqual(self.request('GET',path)[0],404)
            self.assertEqual(self.request('POST',path,{})[0],404)
        self.assertEqual(self.request('POST','/api/preview',{},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.review.state()['version'],1)

    def test_bad_json_error_is_safe_and_readable(self):
        status,_,raw=self.request('POST','/api/preview',dict(view_digest='sensitive-not-echoed',indices=[0]))
        self.assertEqual(status,409);self.assertIn('устарел',json.loads(raw)['error']);self.assertNotIn(b'sensitive-not-echoed',raw)
        self.assertNotIn(str(self.review.root),raw.decode());self.assertEqual(self.review.state()['version'],1)


if __name__=='__main__':unittest.main()
