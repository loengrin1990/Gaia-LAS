"""Real Handler/intake/OC bridge and read-only evidence; captured synthetic response only."""
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

from gaia.controlled_intake import ControlledIntake
from gaia.context_compiler import ContextCompiler
from gaia.operational_context import OperationalContextStore, SafeProvenance
from gaia.operational_context_review import OperationalContextCandidateStore, OperationalContextReviewService, new_candidate
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService
from gaia.server import Handler

FIXTURES=Path(__file__).parent/'fixtures'

class OperationalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ProvenanceStore(Path(self.tmp.name)/'store');self.intake=ControlledIntake(self.store)
        self.project='synthetic';case=json.loads((FIXTURES/'core_local_holdout_20261113.json').read_text())['cases'][0]
        self.text=case['text'];admitted=self.intake.admit(self.project,[('artificial.txt',self.text.encode())]);self.ws=admitted['workspace_id'];self.artifact=admitted['materials'][0]['sanitized_id']
        review=ReviewService(self.store,self.ws,lambda _: {'status':'completed','findings':[]});review.start(self.artifact);review.confirm(self.artifact)
        recorded=json.loads((FIXTURES/'core_local_holdout_20261113_results.json').read_text())['cases'][0];calls=iter(recorded['compiler_calls'])
        with patch('gaia.context_compiler.execute_context_model_call',side_effect=lambda *a,**k:next(calls)):
            self.assertEqual(ContextCompiler(self.store,self.ws).compile(self.artifact),[])
        self.candidates=OperationalContextCandidateStore(self.store.root);self.oc=OperationalContextStore(self.store.root);self.service=OperationalContextReviewService(self.oc,self.candidates);self.candidate=self.candidates.list_scope(scope='project',scope_ref=self.ws,state='pending')[0]
        for target in ['gaia.controlled_intake.default_store','gaia.server.Handler.log_message','gaia.context_compiler.execute_context_model_call','gaia.module_assist.call_lm_studio_with_deadline']:
            mock=patch(target,return_value=self.store) if target.endswith('default_store') else patch(target,side_effect=AssertionError('Model must never run')) if 'call' in target else patch(target)
            mock.start();self.addCleanup(mock.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start();self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.worker.join(timeout=3)

    def snapshot(self):
        return {str(p.relative_to(self.store.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.store.root.rglob('*') if p.is_file()}

    def request(self,path,body=None,cookie=''):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);host=f'127.0.0.1:{self.server.server_port}'
        connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers={'Content-Type':'application/json','Host':host,'Origin':'http://'+host,'Cookie':cookie})
        response=connection.getresponse();raw=response.read();headers=dict(response.getheaders());connection.close();return response.status,raw,headers

    def evidence(self,item_id=None,category='pending',project=None):
        return self.request('/api/operational-context/review/'+category+'/'+(item_id or self.candidate.id)+'/evidence?project='+quote(self.project if project is None else project))

    def assert_unavailable(self,**kwargs):
        before=self.snapshot();status,raw,_=self.evidence(**kwargs);self.assertEqual(status,404,raw);self.assertNotIn(self.text.encode(),raw);self.assertNotIn(self.artifact.encode(),raw);self.assertIn('Другой материал не подставлен',raw.decode());self.assertEqual(before,self.snapshot())

    def test_pending_then_http_confirmed_card_read_same_exact_material_without_writes(self):
        before=self.snapshot()
        for _ in range(2):
            status,raw,headers=self.evidence();self.assertEqual(status,200,raw);data=json.loads(raw);self.assertEqual(data,{'statement':self.candidate.value,'cleaned_text':self.text,'precision':'whole_material','export_allowed':False});self.assertIn('no-store',headers['Cache-Control'])
        self.assertEqual(before,self.snapshot());self.assertFalse(self.oc.list_scope(scope='project',scope_ref=self.ws))
        _,_,headers=self.request('/');cookie=headers['Set-Cookie'].split(';',1)[0]
        status,raw,_=self.request('/api/operational-context/review/'+self.candidate.id+'/confirm',{'project':self.project},cookie);self.assertEqual(status,200,raw);item=json.loads(raw)['current_context'][0]
        before=self.snapshot();status,raw,_=self.evidence(item_id=item['id'],category='current');self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['cleaned_text'],self.text);self.assertEqual(before,self.snapshot())
        self.assert_unavailable()  # Decided candidate cannot impersonate pending.

    def test_foreign_or_missing_workspace_never_resolves_another_source(self):
        self.intake._workspace_for('other')
        for project in ['other','missing','']:
            with self.subTest(project=project):self.assert_unavailable(project=project)
        self.assertIsNone(self.intake.existing_workspace('missing'))

    def test_foreign_source_pointer_cannot_be_laundered_by_own_pending_or_confirmed_card(self):
        other=self.intake._workspace_for('other')
        candidate=new_candidate(scope='project',scope_ref=other,kind='risk',subject_ref='foreign_source',value='Synthetic other statement',provenance=SafeProvenance(source_ref=self.artifact,candidate_ref='foreign_candidate'))
        self.candidates.add(candidate)
        self.assert_unavailable(item_id=candidate.id,project='other')
        item=self.service.confirm(candidate.id,actor_ref='local')
        self.assert_unavailable(item_id=item['id'],category='current',project='other')

    def test_changed_sanitized_extraction_or_raw_source_fails_closed_without_mutation(self):
        san=self.store.object_metadata(self.ws,self.artifact);ext=self.store.object_metadata(self.ws,san['parents'][0]);src=self.store.source_metadata(self.ws,ext['parents'][0])
        paths=[self.store.root/'sanitized'/self.ws/(self.artifact+'.txt'),self.store.root/'artifacts'/self.ws/(ext['id']+'.txt'),self.store.source_path(self.ws,src['id'])]
        for path in paths:
            with self.subTest(kind=path.parent.parent.name):
                content=path.read_bytes();path.write_bytes(content+b' altered');self.assert_unavailable();path.write_bytes(content)

    def test_withdrawn_or_superseded_source_never_substitutes_current_similar_text(self):
        san=self.store.object_metadata(self.ws,self.artifact);ext=self.store.object_metadata(self.ws,san['parents'][0]);src=self.store.source_metadata(self.ws,ext['parents'][0])
        self.store._update(src['id'],current=False);self.assert_unavailable();self.store._update(src['id'],current=True)
        self.intake.admit(self.project,[('artificial.txt',(self.text+' Новый текст.').encode())]);self.assert_unavailable()

    def test_missing_unconfirmed_or_new_cleaned_version_is_unavailable(self):
        state_path=self.store.root/'metadata/review_state.json';original=state_path.read_bytes();state=json.loads(original);state[self.artifact]['confirmed']=False;state_path.write_text(json.dumps(state));self.assert_unavailable();state_path.write_bytes(original)
        state_path.unlink();self.assert_unavailable();self.assertFalse(state_path.exists());state_path.write_bytes(original)
        self.store.create_sanitized(self.ws,self.store.object_metadata(self.ws,self.artifact)['parents'][0],'new-rules',self.text+' Новая версия.');self.assert_unavailable()

    def test_candidate_only_raw_extraction_and_memory_provenance_are_not_material_evidence(self):
        san=self.store.object_metadata(self.ws,self.artifact);ext=self.store.object_metadata(self.ws,san['parents'][0]);refs=['',ext['id'],ext['parents'][0],'memory_entity']
        for index,source in enumerate(refs):
            candidate=new_candidate(scope='project',scope_ref=self.ws,kind='risk',subject_ref='unsupported_'+str(index),value='Synthetic only',provenance=SafeProvenance(source_ref=source,candidate_ref='candidate_'+str(index)))
            self.candidates.add(candidate);self.assert_unavailable(item_id=candidate.id)

    def test_missing_files_and_retired_items_are_unavailable(self):
        path=self.store.root/'sanitized'/self.ws/(self.artifact+'.txt');content=path.read_bytes();path.unlink();self.assert_unavailable();path.write_bytes(content)
        item=self.service.confirm(self.candidate.id,actor_ref='local');self.service.retire(scope='project',scope_ref=self.ws,item_id=item['id'],actor_ref='local');self.assert_unavailable(item_id=item['id'],category='current')
