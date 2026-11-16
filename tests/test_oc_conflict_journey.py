"""New synthetic review cases; real HTTP/store, authored proposals, no model evaluation."""
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
from gaia.operational_context import OperationalContextStore, SafeProvenance
from gaia.operational_context_review import OperationalContextCandidateStore, OperationalContextReviewService, new_candidate
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService
from gaia.server import Handler

CASES=json.loads((Path(__file__).parent/'fixtures/oc_conflict_review_20261117.json').read_text())['cases']

class ConflictEvidenceJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ProvenanceStore(Path(self.tmp.name)/'store');self.intake=ControlledIntake(self.store);self.project='Synthetic conflict';self.candidates=OperationalContextCandidateStore(self.store.root);self.oc=OperationalContextStore(self.store.root);self.artifacts={}
        for case in CASES:
            admitted=self.intake.admit(self.project,[(case['id']+'.txt',case['source'].encode())]);self.ws=admitted['workspace_id'];artifact=admitted['materials'][0]['sanitized_id'];self.artifacts[case['id']]=artifact
            review=ReviewService(self.store,self.ws,lambda _: {'status':'completed','findings':[]});review.start(artifact);self.assertEqual(review.confirm(artifact),case['source'])
        self.add(CASES[0]);self.service=OperationalContextReviewService(self.oc,self.candidates)
        for target in ['gaia.context_compiler.execute_context_model_call','gaia.module_assist.call_lm_studio_with_deadline']:
            mock=patch(target,side_effect=AssertionError('No models in review scenario'));mock.start();self.addCleanup(mock.stop)
        mock=patch('gaia.controlled_intake.default_store',return_value=self.store);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(Handler,'log_message');mock.start();self.addCleanup(mock.stop)
        self.server=None;self.start_server();self.addCleanup(self.stop_server)

    def add(self,case,replaces=''):
        self.candidates.add(new_candidate(scope='project',scope_ref=self.ws,kind='decision',subject_ref='stand_acceptance_date',value=case['statement'],provenance=SafeProvenance(source_ref=self.artifacts[case['id']],candidate_ref='fixture_'+case['id']),candidate_id=case['id'],replaces_id=replaces,sensitivity='unknown'))

    def start_server(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        _,_,headers=self.request('/');self.cookie=headers['Set-Cookie'].split(';',1)[0]

    def stop_server(self):
        if self.server:self.server.shutdown();self.server.server_close();self.worker.join(timeout=3);self.server=None

    def request(self,path,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);host=f'127.0.0.1:{self.server.server_port}'
        connection.request('GET' if body is None else 'POST',path,body=json.dumps(body) if body is not None else None,headers={'Content-Type':'application/json','Host':host,'Origin':'http://'+host,'Cookie':getattr(self,'cookie','')})
        response=connection.getresponse();raw=response.read();headers=dict(response.getheaders());connection.close();return response.status,json.loads(raw) if path!='/' else {},headers

    def view(self):return self.request('/api/operational-context/review?project='+quote(self.project))[1]
    def action(self,item,action):
        status,data,_=self.request('/api/operational-context/review/'+item+'/'+action,{'project':self.project});self.assertEqual(status,200,data);return data
    def evidence(self,item,current=False):
        return self.request('/api/operational-context/review/'+('current' if current else 'pending')+'/'+item+'/evidence?project='+quote(self.project))
    def hashes(self):return {str(p.relative_to(self.store.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.store.root.rglob('*') if p.is_file()}

    def test_fact_conflict_insufficient_basis_confirmation_history_and_restart(self):
        before=self.hashes();status,data,_=self.evidence('accepted_fact');self.assertEqual(status,200);self.assertEqual(data['cleaned_text'],CASES[0]['source']);self.assertEqual(before,self.hashes());self.assertEqual(self.view()['current_context'],[])
        original=self.action('accepted_fact','confirm')['current_context'][0];self.add(CASES[1],original['id']);self.add(CASES[2],original['id'])
        before=self.hashes()
        for case in CASES[1:]:
            status,data,_=self.evidence(case['id']);self.assertEqual(status,200);self.assertEqual(data['precision'],'whole_material');self.assertFalse(data['export_allowed']);self.assertEqual(data['cleaned_text'],case['source'])
        status,data,_=self.evidence(original['id'],True);self.assertEqual(status,200);self.assertEqual(data['cleaned_text'],CASES[0]['source']);self.assertEqual(before,self.hashes());self.assertEqual(self.view()['current_context'],[original])
        # Semantic error is discovered by reading the source, never claimed auto-detected.
        self.action('insufficient_basis','reject');view=self.view();replacement=view['requires_decision'][0];self.assertEqual(replacement['id'],'conflicting_letter');self.assertEqual(replacement['replacement_of'],original['id']);self.assertEqual(replacement['previous_content'],CASES[0]['statement'])
        view=self.action('conflicting_letter','confirm');current=view['current_context'][0];self.assertNotEqual(current['id'],original['id']);self.assertEqual(current['content'],CASES[1]['statement']);self.assertTrue(any(x['content']==CASES[0]['statement'] and x['status']=='Заменено' for x in view['history']));self.assertTrue(any(x['content']==CASES[2]['statement'] and x['status']=='Отклонено' for x in view['history']))
        self.stop_server();self.store=ProvenanceStore(self.store.root);self.start_server();self.assertEqual(self.view(),view)
        rows=OperationalContextStore(self.store.root).list_scope(scope='project',scope_ref=self.ws);self.assertEqual(sum(x['lifecycle']=='active' for x in rows),1);self.assertEqual(next(x for x in rows if x['id']==current['id'])['provenance']['source_ref'],self.artifacts['conflicting_letter'])
        status,data,_=self.evidence(current['id'],True);self.assertEqual(status,200);self.assertEqual(data['cleaned_text'],CASES[1]['source'])
        before=self.hashes();self.assertEqual(self.evidence(original['id'],True)[0],404);self.assertEqual(before,self.hashes())  # Never substitute the new source for historical current ID.

    def test_stale_prior_fact_link_fails_instead_of_opening_replacement_source(self):
        original=self.action('accepted_fact','confirm')['current_context'][0];self.add(CASES[1],original['id']);old_card=self.view()['requires_decision'][0]
        current=self.action('conflicting_letter','confirm')['current_context'][0];before=self.hashes();status,data,_=self.evidence(old_card['replacement_of'],True);self.assertEqual(status,404);self.assertNotIn('cleaned_text',data);self.assertEqual(before,self.hashes());self.assertNotEqual(old_card['replacement_of'],current['id'])
