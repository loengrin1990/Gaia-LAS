"""Synthetic closed-fixture adapter against actual ContextDesk provenance/history."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_review as ui
from scripts import tristan_gaia_trial as gaia


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.trial=gaia.create();self.root=self.trial.root
        self.addCleanup(shutil.rmtree,self.root);self.addCleanup(lambda:self.trial.close())
    def prepare(self):return self.trial.action('prepare',dict(view_digest=self.trial.state()['view_digest']))
    def preview(self,indices=None):return self.trial.action('preview',dict(view_digest=self.trial.state()['view_digest'],indices=[0] if indices is None else indices))
    def accept(self,preview):return self.trial.action('confirm',dict(view_digest=self.trial.state()['view_digest'],indices=preview['indices'],preview_token=preview['preview_token'],confirm=True))
    def restart(self):self.trial.close();self.trial=gaia.Trial(self.root)

    def test_ready_is_real_gaia_memory_with_provenance_and_explicit_prepare(self):
        self.assertEqual(self.trial.state()['phase'],'ready');self.assertEqual(self.trial.desk.working_memory()['version'],1)
        self.assertEqual(len(self.trial.desk.state['uploads']),3)
        before=self.trial.desk.state_path.read_bytes();self.trial.workspace();self.assertEqual(before,self.trial.desk.state_path.read_bytes())
        state=self.prepare();self.assertEqual(state['phase'],'proposed');self.assertEqual(state['version'],1)
        mapping=b.parse((self.root/'binding.json').read_bytes())
        self.assertEqual(mapping['remote_request_id'],self.trial.packet['request_id'])
        self.assertEqual(mapping['remote_packet_digest'],self.trial.packet['packet_digest'])
        self.assertNotEqual(mapping['remote_request_id'],mapping['local_request_id'])
        self.assertEqual(state['sources'],self.trial.packet['documents'])

    def test_partial_accept_updates_same_gaia_store_and_history_after_restart(self):
        self.prepare();preview=self.preview();self.assertEqual(self.trial.desk.working_memory()['version'],1)
        accepted=self.accept(preview)
        self.assertEqual(accepted['memory'],self.trial.packet['before'].replace('Доступ к тестовой среде ожидается.','Доступ к тестовой среде открыт.'))
        self.restart();workspace=self.trial.workspace()
        self.assertEqual(workspace['state']['version'],2);self.assertEqual(len(workspace['history']),2)
        self.assertEqual(workspace['history'][0]['text'],accepted['memory']);self.assertEqual(workspace['history'][1]['text'],self.trial.packet['before'])
        self.assertEqual(workspace['state']['accepted_indices'],[0])
        with self.assertRaises(b.BridgeError):self.accept(preview)

    def test_repeated_prepare_and_duplicate_upload_do_not_create_request_or_version(self):
        old=self.trial.state()['view_digest'];state=self.prepare()
        with self.assertRaises(b.BridgeError):self.trial.action('prepare',dict(view_digest=old))
        self.prepare();self.assertEqual(len([r for r in self.trial.desk._memory_state()['requests'] if r.get('mode')=='addressed_v1']),1)
        import base64
        doc=self.trial.packet['documents'][0]
        upload=self.trial.desk.upload(dict(name=doc['name'],data=base64.b64encode(doc['text'].encode()).decode()))
        self.assertTrue(upload['duplicate']);self.assertEqual(len(self.trial.desk.state['uploads']),3)
        self.assertEqual(self.trial.state()['version'],1)

    def test_interruption_between_prepare_and_import_resumes_same_request(self):
        with patch.object(self.trial.pilot,'propose',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):self.prepare()
        _,request=self.trial._request();identifier=request['id'];self.assertEqual(request['status'],'addressed_prepared')
        self.restart();self.assertEqual(self.trial.state()['phase'],'prepared');self.prepare()
        self.assertEqual(self.trial._request()[1]['id'],identifier);self.assertEqual(self.trial.state()['version'],1)

    def test_interruption_before_preview_or_confirm_never_accepts(self):
        self.prepare();self.restart();self.assertEqual(self.trial.state()['phase'],'proposed')
        old=self.preview();self.restart()
        with self.assertRaises(b.BridgeError):self.accept(old)
        self.assertEqual(self.trial.state()['version'],1)
        self.accept(self.preview());self.assertEqual(self.trial.state()['version'],2)

    def test_cancel_survives_restart_and_cannot_be_reprepared(self):
        self.prepare();preview=self.preview();state=self.trial.state()
        self.trial.action('cancel',dict(view_digest=state['view_digest']));self.restart()
        self.assertEqual(self.trial.state()['phase'],'cancelled');self.assertEqual(self.trial.state()['version'],1)
        self.assertEqual(self.trial.state()['memory'],self.trial.packet['before'])
        self.assertEqual(self.prepare()['phase'],'cancelled')
        with self.assertRaises(b.BridgeError):self.accept(preview)

    def test_selection_and_false_ack_mismatch_fail_without_memory_write(self):
        self.prepare();preview=self.preview();before=self.trial.desk.state_path.read_bytes()
        for changes in [dict(indices=[1]),dict(indices=[False]),dict(confirm=False),dict(preview_token='wrong')]:
            preview=self.preview();body=dict(view_digest=self.trial.state()['view_digest'],indices=[0],preview_token=preview['preview_token'],confirm=True);body.update(changes)
            with self.assertRaises(b.BridgeError):self.trial.action('confirm',body)
        self.assertEqual(before,self.trial.desk.state_path.read_bytes())

    def test_local_packet_or_binding_corruption_blocks_confirmation(self):
        self.prepare();preview=self.preview();mapping=b.parse((self.root/'binding.json').read_bytes());mapping['remote_packet_digest']='0'*64;b.atomic(self.root/'binding.json',mapping)
        with self.assertRaises(b.BridgeError):self.accept(preview)
        self.assertEqual(self.trial.desk._memory_state()['version'],1)

    def test_changed_source_and_local_version_fail_closed(self):
        self.prepare();preview=self.preview();ref=self.trial.record['refs'][0]
        upload=next(x for x in self.trial.desk.state['uploads'] if x['id']==ref['id'])
        path=self.trial.desk.store.root/'sanitized'/self.trial.desk.state['workspace']/(upload['artifact']+'.txt')
        old=path.read_bytes();path.write_text('Changed synthetic source')
        with self.assertRaisesRegex(ValueError,'повреждён'):self.accept(preview)
        self.assertEqual(self.trial.desk._memory_state()['version'],1)
        path.write_bytes(old)
        memory=self.trial.desk._memory_state();self.trial.desk._commit_memory({**memory,'version':2})
        with self.assertRaises(b.BridgeError):self.trial.state()

    def test_incomplete_remote_response_never_gets_local_proposal(self):
        demo=ui.demo();self.addCleanup(shutil.rmtree,demo.root)
        data=b.parse((demo.root/'session.json').read_bytes());data['phase']='prepared';data['response']['status']='incomplete';b.atomic(demo.root/'session.json',data)
        with self.assertRaises(b.BridgeError):gaia.create(demo.root)
        self.assertEqual(self.trial.state()['phase'],'ready')

    def test_empty_answer_closes_without_new_memory_version(self):
        trial=gaia.create(kind='unsupported');self.addCleanup(shutil.rmtree,trial.root);self.addCleanup(trial.close)
        state=trial.action('prepare',dict(view_digest=trial.state()['view_digest']))
        self.assertEqual(state['phase'],'no_changes');self.assertEqual(state['version'],1);self.assertEqual(len(trial.workspace()['history']),1)

    def test_replay_uses_original_binding_without_changing_source_store(self):
        demo=ui.demo();self.addCleanup(shutil.rmtree,demo.root);before=(demo.root/'session.json').read_bytes()
        replay=gaia.create(demo.root);self.addCleanup(shutil.rmtree,replay.root);self.addCleanup(replay.close)
        self.assertEqual(replay.packet,b.parse(before)['packet']);self.assertEqual(replay.record['mode'],'recorded_replay')
        replay.action('prepare',dict(view_digest=replay.state()['view_digest']))
        self.assertEqual((demo.root/'session.json').read_bytes(),before)

    def test_only_one_process_and_synthetic_roots_are_accepted(self):
        with self.assertRaises(BlockingIOError):gaia.Trial(self.root)
        with self.assertRaises(b.BridgeError):gaia.Trial(Path('/Users/ilia/Documents/Gaia'))
        self.assertEqual(self.trial.state()['version'],1)


class TrialHttpTests(unittest.TestCase):
    def test_shutdown_drains_active_mutation_before_store_can_be_released(self):
        trial=gaia.create();server=ui.make_server(trial,workspace=True)
        self.addCleanup(shutil.rmtree,trial.root);self.addCleanup(trial.close)
        self.assertFalse(server.daemon_threads)
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        # Exercise the server's real worker lifecycle, without a model or timed sleeps.
        original=server.finish_request
        server.finish_request=lambda *_:(entered.set(),release.wait(3))
        import socket
        a,z=socket.socketpair()
        worker=threading.Thread(target=lambda:server.process_request(a,('127.0.0.1',0)))
        worker.start();self.assertTrue(entered.wait(1));worker.join(1)
        closer=threading.Thread(target=lambda:(server.server_close(),finished.set()));closer.start()
        try:self.assertFalse(finished.wait(.05))
        finally:release.set();closer.join(3);z.close();server.finish_request=original
        self.assertTrue(finished.is_set())

    def test_workspace_to_review_and_back_uses_same_persisted_memory(self):
        trial=gaia.create();server=ui.make_server(trial,workspace=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(shutil.rmtree,trial.root);self.addCleanup(trial.close)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port=server.server_port;cookie=None
        def request(method,path,body=None):
            nonlocal cookie
            headers={'Host':f'127.0.0.1:{port}','Origin':f'http://127.0.0.1:{port}'}
            if cookie:headers['Cookie']=cookie
            if body is not None:headers['Content-Type']='application/json'
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            try:
                connection.request(method,path,b.encode(body) if body else None,headers);response=connection.getresponse();raw=response.read()
                if response.getheader('Set-Cookie'):cookie=response.getheader('Set-Cookie').split(';')[0]
                return response.status,raw
            finally:connection.close()
        status,raw=request('GET','/workspace');self.assertEqual(status,200);self.assertIn('История принятой памяти',raw.decode())
        state=json.loads(request('GET','/api/workspace')[1])['state']
        state=json.loads(request('POST','/api/prepare',dict(view_digest=state['view_digest']))[1]);self.assertEqual(state['phase'],'proposed')
        preview=json.loads(request('POST','/api/preview',dict(view_digest=state['view_digest'],indices=[0]))[1])
        self.assertEqual(trial.state()['version'],1)
        status,raw=request('POST','/api/confirm',dict(view_digest=state['view_digest'],indices=[0],preview_token=preview['preview_token'],confirm=True));self.assertEqual(status,200)
        workspace=json.loads(request('GET','/api/workspace')[1]);self.assertEqual(workspace['state']['version'],2);self.assertEqual(len(workspace['history']),2)
        for path in ['/api/send','/api/desk/upload','/api/desk/addressed/analyze','/config.json']:
            self.assertEqual(request('POST',path,{})[0],404)


if __name__=='__main__':unittest.main()
