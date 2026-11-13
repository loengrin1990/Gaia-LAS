"""Replay a captured real local answer at the transport boundary, with real routing."""
import json
import http.client
from http.server import ThreadingHTTPServer
from urllib.parse import quote
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.context_compiler import ContextService, context_compile_outcome
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService
from gaia.operational_context import OperationalContextStore
from gaia.operational_context_review import OperationalContextCandidateStore
from gaia.jobs import JOBS, JOBS_LOCK, JOB_CANCEL_EVENTS, JOB_CAPACITY, _run_context_compile_job, get_job
from gaia.models import JobRecord
from gaia.jobs import local_now
from gaia.server import Handler

FIXTURES = Path(__file__).parent / 'fixtures'


class ContextCompileRoutingStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ProvenanceStore(Path(self.temporary.name))
        self.intake = ControlledIntake(self.store)
        self.corpus = json.loads((FIXTURES / 'core_local_holdout_20261113.json').read_text())['cases']
        self.responses = json.loads((FIXTURES / 'core_local_holdout_20261113_results.json').read_text())['cases']
        self.job_ids = []
        self.addCleanup(self.clean_jobs)

    def clean_jobs(self):
        with JOBS_LOCK:
            for job_id in self.job_ids:
                JOBS.pop(job_id, None)
                JOB_CANCEL_EVENTS.pop(job_id, None)

    def prepare(self, index):
        source = self.corpus[index]
        observed = self.responses[index]
        project = 'qualification-' + source['id']
        admitted = self.intake.admit(project, [('synthetic.txt', source['text'].encode())])
        workspace = admitted['workspace_id']
        artifact = admitted['materials'][0]['sanitized_id']
        review = ReviewService(self.store, workspace, lambda _: json.loads(observed['veil_response']['answer']))
        review.start(artifact)
        self.assertEqual(review.confirm(artifact), observed['sanitized_text'])
        return project, workspace, artifact, observed

    def run_job(self, project, artifact):
        job_id = 'routing-test-' + str(len(self.job_ids))
        self.job_ids.append(job_id)
        now = local_now()
        with JOBS_LOCK:
            JOBS[job_id] = JobRecord(id=job_id, status='created', created_at=now, updated_at=now, project=project, message='', progress=0, job_type='context_compile', timeout_seconds=60)
            JOB_CANCEL_EVENTS[job_id] = threading.Event()
        self.assertTrue(JOB_CAPACITY.acquire(blocking=False))
        with patch('gaia.controlled_intake.default_store', return_value=self.store):
            _run_context_compile_job(job_id, project, artifact)
        return get_job(job_id)

    def http_status(self, project, artifact):
        with patch('gaia.controlled_intake.default_store', return_value=self.store), patch.object(Handler, 'log_message'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request('GET', '/api/context/' + artifact + '/status?project=' + quote(project))
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                result = json.loads(response.read())
                connection.close()
                return result
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

    def test_real_grounded_decision_in_oc_queue_is_not_empty_and_repeat_is_receipt_only(self):
        project, workspace, artifact, observed = self.prepare(0)
        calls = iter(observed['compiler_calls'])
        def replay(payload, *args, **kwargs):
            recorded = next(calls)
            self.assertEqual(recorded['operation'], payload.get('operation', 'generate'))
            return recorded
        with patch('gaia.context_compiler.execute_context_model_call', side_effect=replay) as transport:
            job = self.run_job(project, artifact)
            self.assertEqual(job.status, 'done', job.error)
            self.assertEqual(job.result['candidates'], [])  # OC authority never duplicated into legacy.
            self.assertEqual(job.result['operational_context_count'], 1)
            self.assertEqual(job.candidate_count, 1)
            count = transport.call_count
            repeated = self.run_job(project, artifact)
            self.assertEqual(repeated.status, 'done')
            self.assertEqual(repeated.result, job.result)
            self.assertEqual(transport.call_count, count)
        pending = OperationalContextCandidateStore(self.store.root).list_scope(scope='project', scope_ref=workspace, state='pending')
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].provenance.source_ref, artifact)
        self.assertIn('локальном архиве', pending[0].value)
        self.assertFalse(OperationalContextStore(self.store.root).list_scope(scope='project', scope_ref=workspace))
        self.assertFalse(any(ContextService(self.store, workspace).summary().values()))
        # Reopened review uses the receipt, including after in-memory jobs disappear.
        self.clean_jobs()
        status = self.http_status(project, artifact)
        self.assertEqual(status['status'], 'done')
        self.assertEqual(status['candidate_count'], 1)
        self.assertEqual(status['operational_context_count'], 1)
        self.assertNotIn('не предложила', status['user_message'])

    def test_real_empty_answer_is_known_zero_in_both_queues(self):
        project, workspace, artifact, observed = self.prepare(5)
        calls = iter(observed['compiler_calls'])
        with patch('gaia.context_compiler.execute_context_model_call', side_effect=lambda *a, **k: next(calls)):
            job = self.run_job(project, artifact)
        self.assertEqual(job.status, 'complete_empty', job.error)
        self.assertEqual(job.candidate_count, 0)
        self.assertEqual(job.result['operational_context_count'], 0)
        self.assertIn('не гарантирует', job.message)
        self.assertEqual(self.http_status(project, artifact)['status'], 'complete_empty')
        self.assertFalse(OperationalContextCandidateStore(self.store.root).list_scope(scope='project', scope_ref=workspace, state='pending'))

    def test_historical_receipt_without_diagnostics_cannot_claim_empty(self):
        project, _, artifact, observed = self.prepare(5)
        calls = iter(observed['compiler_calls'])
        with patch('gaia.context_compiler.execute_context_model_call', side_effect=lambda *a, **k: next(calls)):
            self.assertEqual(self.run_job(project, artifact).status, 'complete_empty')
        compiler = self.intake.compiler(project)
        path = compiler._receipt_path(artifact)
        receipt = json.loads(path.read_text())
        del receipt['producer_diagnostics']
        path.write_text(json.dumps(receipt))
        with patch('gaia.context_compiler.execute_context_model_call', side_effect=AssertionError('receipt must avoid model calls')):
            job = self.run_job(project, artifact)
        self.assertEqual(job.status, 'done')
        self.assertIsNone(job.result['operational_context_count'])
        self.assertEqual(job.result['context_status'], 'ready')
        self.assertEqual(self.http_status(project, artifact)['status'], 'done')

    def test_legacy_and_mixed_counts_do_not_turn_pending_into_accepted(self):
        for legacy, oc, status, total in [(1, 0, 'done', 1), (1, 2, 'done', 3), (0, 0, 'complete_empty', 0)]:
            with self.subTest(legacy=legacy, oc=oc):
                receipt = {'candidate_count': legacy, 'producer_diagnostics': [{'bridge': {'stage': 'bridge_accepted', 'reason': 'equivalent_current_noop'}}] * oc}
                outcome = context_compile_outcome(receipt)
                self.assertEqual(outcome, {'status': status, 'candidate_count': total, 'operational_context_count': oc})
                self.assertNotIn('confirmed', outcome)
