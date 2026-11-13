"""Two bounded offline rounds on real synthetic ContextDesk, without any model."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

from scripts import tristan_gaia_trial as gaia
from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_review as ui


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.trial=gaia.create();self.root=self.trial.root
        self.addCleanup(shutil.rmtree,self.root);self.addCleanup(lambda:self.trial.close())
    def body(self):return dict(view_digest=self.trial.state()['view_digest'])
    def prepare(self):return self.trial.action('prepare',self.body())
    def preview(self,indices):return self.trial.action('preview',dict(**self.body(),indices=indices))
    def accept(self,indices):
        p=self.preview(indices);return self.trial.action('confirm',dict(**self.body(),indices=indices,preview_token=p['preview_token'],confirm=True))
    def first(self):self.prepare();return self.accept([0])
    def advance(self):return self.trial.action('advance',self.body())
    def restart(self):self.trial.close();self.trial=gaia.Trial(self.root)

    def test_two_rounds_have_new_binding_current_before_and_literal_history(self):
        first=self.first();old_packet=copy.deepcopy(self.trial.packet);self.advance();second=self.trial.state()
        self.assertEqual(second['before'],first['memory']);self.assertEqual(second['version'],2);self.assertEqual(second['round_number'],2)
        self.assertNotEqual(self.trial.packet['request_id'],old_packet['request_id']);self.assertNotEqual(self.trial.packet['packet_digest'],old_packet['packet_digest'])
        self.assertEqual(self.trial.packet['base_version'],2);self.assertEqual(second['origin'],'offline_followup')
        with self.assertRaises(b.BridgeError):b.validate_packet(self.trial.packet) # Original v1 external gate stays closed.
        self.prepare();accepted=self.accept([0,1]);self.assertEqual(accepted['version'],3)
        self.assertIn('Инструкция проверки подготовлена и опубликована.',accepted['memory']);self.assertIn('Проверка пользовательских прав завершена.',accepted['memory'])
        for fact in ['Доступ к тестовой среде открыт.','Координатор собирает замечания.','Работа разрешена после проверки пользовательских прав.']:
            self.assertIn(fact,accepted['memory'])
        self.restart();workspace=self.trial.workspace();self.assertEqual([x['version'] for x in workspace['history']],[3,2,1])
        self.assertEqual(workspace['history'][1]['text'],first['memory']);self.assertFalse(workspace['state']['can_advance'])
        self.assertEqual(len(workspace['passes']),2)

    def test_cancel_second_keeps_v2_and_closed_history_after_restart(self):
        first=self.first();self.advance();self.prepare();preview=self.preview([0])
        self.trial.action('cancel',self.body());self.restart()
        state=self.trial.state();self.assertEqual(state['version'],2);self.assertEqual(state['phase'],'cancelled');self.assertEqual(state['memory'],first['memory'])
        self.assertEqual(len(self.trial.workspace()['history']),2)
        with self.assertRaises(b.BridgeError):self.trial.action('confirm',dict(**self.body(),indices=[0],preview_token=preview['preview_token'],confirm=True))
        with self.assertRaises(b.BridgeError):self.advance()

    def test_old_preview_and_old_requests_cannot_mutate_current_round(self):
        self.prepare();old_view=self.body();old_preview=self.preview([0]);self.accept([0]);closed=self.body();self.advance()
        for action,payload in [('prepare',old_view),('advance',closed),('cancel',old_view),('confirm',dict(**old_view,indices=[0],preview_token=old_preview['preview_token'],confirm=True))]:
            with self.subTest(action=action),self.assertRaises(b.BridgeError):self.trial.action(action,payload)
        self.prepare()
        with self.assertRaises(b.BridgeError):self.trial.action('confirm',dict(**self.body(),indices=[0],preview_token=old_preview['preview_token'],confirm=True))
        self.assertEqual(self.trial.state()['version'],2)

    def test_first_answer_cannot_be_rebound_into_second_manifest(self):
        self.first();self.advance();path=self.root/'next.json';saved=b.parse(path.read_bytes());saved['response']=self.trial.record['response'];b.atomic(path,saved)
        with self.assertRaises(b.BridgeError):self.trial.state()
        self.assertEqual(self.trial.desk._memory_state()['version'],2)

    def test_old_id_or_recomputed_changed_packet_is_refused(self):
        self.first();self.advance();path=self.root/'next.json';original=b.parse(path.read_bytes())
        for kind in ['old_id','changed_base']:
            saved=copy.deepcopy(original)
            if kind=='old_id':saved['packet']['request_id']=self.trial.first_packet['request_id']
            else:saved['packet']['base_version']=1
            saved['packet']['packet_digest']=b.digest({k:v for k,v in saved['packet'].items() if k!='packet_digest'})
            b.atomic(path,saved)
            with self.subTest(kind=kind),self.assertRaises(b.BridgeError):self.trial.state()
        b.atomic(path,original)

    def test_double_click_and_fresh_duplicate_advance_cannot_add_rounds_or_files(self):
        self.first();old=self.body();self.advance()
        with self.assertRaises(b.BridgeError):self.trial.action('advance',old)
        with self.assertRaises(b.BridgeError):self.advance()
        self.prepare();self.prepare()
        self.assertEqual(len(self.trial.desk.state['uploads']),4)
        self.assertEqual(len([r for r in self.trial.desk._memory_state()['requests'] if r.get('mode')=='addressed_v1']),2)
        self.assertEqual(self.trial.state()['version'],2)

    def test_interrupted_upload_before_manifest_retries_without_duplicate(self):
        self.first();atomic=b.atomic
        def fail(path,value):
            if path.name=='next.json':raise OSError('interrupted')
            return atomic(path,value)
        with patch.object(b,'atomic',side_effect=fail),self.assertRaises(OSError):self.advance()
        self.assertFalse((self.root/'next.json').exists());self.assertEqual(len(self.trial.desk.state['uploads']),4)
        self.restart();self.advance();self.assertEqual(len(self.trial.desk.state['uploads']),4)
        self.assertEqual(self.trial.state()['round_number'],2)

    def test_lost_advance_response_resumes_existing_packet(self):
        self.first();old=self.body();atomic=b.atomic
        def fail_after(path,value):
            atomic(path,value)
            if path.name=='next.json':raise OSError('response interrupted')
        with patch.object(b,'atomic',side_effect=fail_after),self.assertRaises(OSError):self.advance()
        packet=b.parse((self.root/'next.json').read_bytes())['packet']
        self.restart();self.assertEqual(self.trial.packet,packet)
        with self.assertRaises(b.BridgeError):self.trial.action('advance',old)
        self.assertEqual(len(self.trial.desk.state['uploads']),4)

    def test_interrupted_second_import_and_lost_preview_resume_safely(self):
        self.first();self.advance()
        with patch.object(self.trial.pilot,'propose',side_effect=RuntimeError('interrupted')),self.assertRaises(RuntimeError):self.prepare()
        request=self.trial._request()[1]['id'];self.restart();self.assertEqual(self.trial.state()['phase'],'prepared');self.prepare()
        self.assertEqual(self.trial._request()[1]['id'],request);preview=self.preview([0]);self.restart()
        with self.assertRaises(b.BridgeError):self.trial.action('confirm',dict(**self.body(),indices=[0],preview_token=preview['preview_token'],confirm=True))
        self.assertEqual(self.trial.state()['version'],2);self.accept([0]);self.assertEqual(self.trial.state()['version'],3)

    def test_second_source_change_blocks_acceptance(self):
        self.first();self.advance();self.prepare();preview=self.preview([0]);ref=self.trial.active_record['refs'][0]
        upload=next(x for x in self.trial.desk.state['uploads'] if x['id']==ref['id']);path=self.trial.desk.store.root/'sanitized'/self.trial.desk.state['workspace']/(upload['artifact']+'.txt');path.write_text('Changed synthetic letter')
        with self.assertRaises(ValueError):self.trial.action('confirm',dict(view_digest='old',indices=[0],preview_token=preview['preview_token'],confirm=True))
        self.assertEqual(self.trial.desk._memory_state()['version'],2)

    def test_accepted_old_pending_rights_fact_is_replaced_not_contradicted(self):
        source=ui.demo();self.addCleanup(shutil.rmtree,source.root);data=b.parse((source.root/'session.json').read_bytes());answer=json.loads(data['response']['output'][0]['content'][0]['text'])
        pending='Проверка пользовательских прав ещё не выполнена.';answer['operations'].append(dict(action='append',old_text='',new_text=pending,quote=pending))
        data['response']=ui.envelope(data['packet'],answer['operations']);b.atomic(source.root/'session.json',data)
        trial=gaia.create(source.root);self.addCleanup(shutil.rmtree,trial.root);self.addCleanup(trial.close)
        trial.action('prepare',dict(view_digest=trial.state()['view_digest']));s=trial.state();p=trial.action('preview',dict(view_digest=s['view_digest'],indices=[0,1,2]));trial.action('confirm',dict(view_digest=s['view_digest'],indices=[0,1,2],preview_token=p['preview_token'],confirm=True))
        trial.action('advance',dict(view_digest=trial.state()['view_digest']));self.assertEqual(trial.candidate['operations'][1]['action'],'replace')
        trial.action('prepare',dict(view_digest=trial.state()['view_digest']));s=trial.state();p=trial.action('preview',dict(view_digest=s['view_digest'],indices=[0,1]));state=trial.action('confirm',dict(view_digest=s['view_digest'],indices=[0,1],preview_token=p['preview_token'],confirm=True))
        self.assertNotIn(pending,state['memory']);self.assertEqual(state['memory'].count('Проверка пользовательских прав завершена.'),1)

    def test_http_stale_second_tab_and_back_cannot_start_hidden_duplicate(self):
        self.first();server=ui.make_server(self.trial,workspace=True);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port=server.server_port;cookie=None
        def request(method,path,body=None):
            nonlocal cookie
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3);headers={'Host':f'127.0.0.1:{port}','Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'}
            if cookie:headers['Cookie']=cookie
            try:
                connection.request(method,path,b.encode(body) if body else None,headers);response=connection.getresponse();raw=response.read()
                if response.getheader('Set-Cookie'):cookie=response.getheader('Set-Cookie').split(';')[0]
                return response.status,raw
            finally:connection.close()
        request('GET','/workspace');old=json.loads(request('GET','/api/state')[1]);body=dict(view_digest=old['view_digest'])
        self.assertEqual(request('POST','/api/advance',body)[0],200)
        for path in ['/api/advance','/api/prepare','/api/cancel']:self.assertEqual(request('POST',path,body)[0],409)
        current=json.loads(request('GET','/api/state')[1]);self.assertEqual(current['round_number'],2)
        self.assertEqual(request('POST','/api/prepare',dict(view_digest=current['view_digest'],response=self.trial.record['response']))[0],409)
        self.assertEqual(len(self.trial.desk.state['uploads']),4);self.assertEqual(self.trial.state()['version'],2)


if __name__=='__main__':unittest.main()
