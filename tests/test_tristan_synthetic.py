"""Synthetic bridge safety tests. No external network or real Gaia store."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_send as paid


def operations():
    return [dict(action='replace', old_text='Доступ к тестовой среде ожидается.',
                 new_text='Доступ к тестовой среде открыт.', quote='Доступ к тестовой среде открыт.'),
            dict(action='replace', old_text='Инструкция проверки ещё не подготовлена.',
                 new_text='Инструкция проверки подготовлена.', quote='Инструкция проверки подготовлена.')]


def envelope(packet, ops=None):
    candidate = dict(request_id=packet['request_id'], base_version=packet['base_version'],
                     packet_digest=packet['packet_digest'], operations=operations() if ops is None else ops)
    return dict(status='completed', error=None, incomplete_details=None,
                output=[dict(type='reasoning', summary=[]), dict(type='message', role='assistant', status='completed',
                        content=[dict(type='output_text', text=json.dumps(candidate, ensure_ascii=False))])],
                usage=dict(input_tokens=400, output_tokens=100, output_tokens_details=dict(reasoning_tokens=50)))


def receipt():
    now = datetime.now(timezone.utc)
    return dict(observed_at=now.isoformat(), grant_expires_at=(now+timedelta(days=3)).isoformat(),
                ai_studio_covered=True, personal_funds_allowed=False, grant_remaining_rub=3000,
                input_rub_per_1000=0.3, output_rub_per_1000=0.5)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.root = b.create('partial'); self.addCleanup(shutil.rmtree, self.root)
        with b.session(self.root) as state: self.packet = copy.deepcopy(state['packet'])

    def state(self):
        return b.parse((self.root/'session.json').read_bytes())

    def test_plain_review_exposes_full_sources_and_hides_reasoning(self):
        candidate=b.reply(self.packet,envelope(self.packet))
        view=dict(packet=self.packet,version=1,phase='proposed',accepted_text=self.packet['before'],
                  review_notice='Проверьте смысл.',candidate=candidate)
        text=b.human_review(view)
        self.assertIn('ОПЕРАЦИЯ 0',text)
        for source in self.packet['documents']:self.assertIn(source['text'],text)
        self.assertIn('Координатор собирает замечания.\nРабота разрешена',text)
        self.assertNotIn('reasoning',text)
        self.assertIn('ОПЕРАЦИЯ 1', b.human_review(b.preview(self.packet,candidate,[1])))
        self.assertNotIn('ОПЕРАЦИЯ 0', b.human_review(b.preview(self.packet,candidate,[1])))
        view['candidate']['operations'][0]['new_text']='\x1b[31m bad\u202e'
        self.assertIn('\\u202e',b.human_review(view))
        self.assertNotIn('\x1b',b.human_review(view))

    def test_closed_synthetic_registry_and_pinned_corpus(self):
        for case in ('unknown', '../role', 'real'): 
            with self.assertRaises(b.BridgeError): b.packet(case, '0'*32)
        with patch.object(b, 'CORPUS_SHA', '0'*64), self.assertRaises(b.BridgeError): b.fixture('partial')
        for key in ('before', 'task', 'documents', 'case_id', 'packet_digest', 'base_version'):
            packet = copy.deepcopy(self.packet); packet[key] = 'arbitrary'
            with self.subTest(key=key), self.assertRaises(b.BridgeError): b.validate_packet(packet)

    def test_conflict_is_refused_before_disclosure(self):
        with self.assertRaisesRegex(b.BridgeError, 'conflict'): b.create('conflict')
        with self.assertRaisesRegex(b.BridgeError, 'conflict'):
            paid.request(b.packet('conflict','a'*32), 'model')

    def test_all_remaining_cases_have_fixed_packets_without_acceptance_oracles(self):
        for case in b.CASES:
            if case == 'conflict': continue
            packet = b.packet(case, 'a'*32)
            b.validate_packet(packet)
            self.assertNotIn('acceptance', packet)
            self.assertEqual(b.fixture(case)['documents'], packet['documents'])

    def test_strict_bindings(self):
        for key, value in [('request_id','0'*32), ('base_version',2), ('base_version',True), ('packet_digest','0'*64)]:
            response = envelope(self.packet); candidate = json.loads(response['output'][1]['content'][0]['text'])
            candidate[key] = value; response['output'][1]['content'][0]['text'] = json.dumps(candidate)
            with self.subTest(key=key,value=value), self.assertRaises(b.BridgeError): b.ingest(self.root,response)
        self.assertEqual(self.state()['phase'], 'prepared')

    def test_legacy_benchmark_response_is_not_rebound(self):
        response = envelope(self.packet)
        response['output'][1]['content'][0]['text'] = json.dumps({'operations':operations()})
        with self.assertRaises(b.BridgeError): b.ingest(self.root,response)

    def test_incomplete_reasoning_only_refusal_and_tools_fail(self):
        variants = []
        response=envelope(self.packet);response['status']='incomplete'; variants.append(response)
        response=envelope(self.packet);response['output']=response['output'][:1]; variants.append(response)
        response=envelope(self.packet);response['output'][1]['content'][0]['type']='refusal';variants.append(response)
        response=envelope(self.packet);response['output'].append({'type':'function_call'});variants.append(response)
        response=envelope(self.packet);response['error']={'code':'error'};variants.append(response)
        response=envelope(self.packet);response['output'].append(response['output'][1]);variants.append(response)
        for response in variants:
            with self.subTest(response=response['status']), self.assertRaises(b.BridgeError): b.ingest(self.root,response)
        self.assertEqual(self.state()['version'],1)

    def test_quote_target_overlap_extra_fields_and_oversize_fail(self):
        bad = []
        ops=operations();ops[0]['quote']='Выдуманная цитата, которой нет.';bad.append(ops)
        ops=operations();ops[0]['old_text']='несуществующий старый текст';bad.append(ops)
        ops=operations();ops[1]=ops[0];bad.append(ops)
        ops=operations();ops[0]['extra']='x';bad.append(ops)
        ops=operations();ops[0]['new_text']='x'*4001;bad.append(ops)
        for ops in bad:
            with self.assertRaises(b.BridgeError): b.ingest(self.root,envelope(self.packet,ops))
        self.assertEqual(self.state()['accepted_text'],self.packet['before'])

    def test_json_duplicates_nan_and_size_fail(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'x'*(b.MAX_BYTES+1),b'not JSON'):
            with self.assertRaises(b.BridgeError): b.parse(raw)

    def test_partial_review_is_read_only_confirm_is_literal_and_restart_safe(self):
        b.ingest(self.root,envelope(self.packet))
        before=(self.root/'session.json').read_bytes()
        preview=b.review(self.root,[0])
        self.assertEqual(before,(self.root/'session.json').read_bytes())
        self.assertEqual(preview['after'],self.packet['before'].replace(operations()[0]['old_text'],operations()[0]['new_text']))
        with self.assertRaises(b.BridgeError): b.confirm(self.root,[0],preview['preview_digest'])
        result=b.confirm(self.root,[0],preview['preview_digest'],True)
        with b.session(self.root) as reopened:
            self.assertEqual(reopened['accepted_text'],result);self.assertEqual(reopened['version'],2)
        self.assertIn('Инструкция проверки ещё не подготовлена.',result)
        self.assertIn('Работа разрешена после проверки пользовательских прав.',result)
        with self.assertRaises(b.BridgeError): b.confirm(self.root,[0],preview['preview_digest'],True)
        with self.assertRaises(b.BridgeError): b.ingest(self.root,envelope(self.packet))

    def test_preview_mismatch_and_invalid_selection_fail(self):
        b.ingest(self.root,envelope(self.packet));preview=b.review(self.root,[0])
        for selected in ([1],[0,0],[-1],[2],[True],[]):
            with self.subTest(selected=selected),self.assertRaises(b.BridgeError):
                b.confirm(self.root,selected,preview['preview_digest'],True)
        self.assertEqual(self.state()['version'],1)

    def test_candidate_change_after_preview_is_rejected(self):
        b.ingest(self.root,envelope(self.packet));preview=b.review(self.root,[0])
        state=self.state();state['response']['output'][1]['content'][0]['text']=json.dumps(dict(
            request_id=self.packet['request_id'],base_version=1,packet_digest=self.packet['packet_digest'],operations=operations()[1:]))
        b.atomic(self.root/'session.json',state)
        with self.assertRaisesRegex(b.BridgeError,'preview_changed'):
            b.confirm(self.root,[0],preview['preview_digest'],True)

    def test_cancel_repeat_late_reply_and_confirm(self):
        b.cancel(self.root);b.cancel(self.root)
        with self.assertRaises(b.BridgeError): b.ingest(self.root,envelope(self.packet))
        with self.assertRaises(b.BridgeError): b.confirm(self.root,[0],'x',True)
        self.assertEqual(self.state()['accepted_text'],self.packet['before'])

    def test_cancel_after_review_saves_nothing(self):
        b.ingest(self.root,envelope(self.packet));preview=b.review(self.root,[0]);b.cancel(self.root)
        with self.assertRaises(b.BridgeError):b.confirm(self.root,[0],preview['preview_digest'],True)
        self.assertEqual(self.state()['version'],1)

    def test_stale_version_source_packet_and_accepted_memory_tamper_fail(self):
        original=self.state()
        for key,value in [('version',2),('accepted_text','different')]:
            state=copy.deepcopy(original);state[key]=value;b.atomic(self.root/'session.json',state)
            with self.assertRaises(b.BridgeError):b.ingest(self.root,envelope(self.packet))
        state=copy.deepcopy(original);state['packet']['documents'][0]['text']+=' new';b.atomic(self.root/'session.json',state)
        with self.assertRaises(b.BridgeError):b.ingest(self.root,envelope(self.packet))

    def test_empty_candidate_does_not_create_new_version(self):
        b.ingest(self.root,envelope(self.packet,[]))
        with self.assertRaises(b.BridgeError):b.review(self.root,[])
        self.assertEqual(self.state()['version'],1)

    def test_unrelated_or_symlink_session_refused(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(b.BridgeError), b.session(Path(other)): pass
            link=Path(other)/'link';link.symlink_to(self.root,target_is_directory=True)
            with self.assertRaises(b.BridgeError), b.session(link):pass


class PaidRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root=b.create('partial');self.addCleanup(shutil.rmtree,self.root)
        with b.session(self.root) as state:self.packet=copy.deepcopy(state['packet'])
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.benchmark=Path(self.temp.name);self.key=self.benchmark/'key';self.key.write_text('test-key')
        self.route=patch.object(paid,'route',return_value=(self.benchmark,self.key,'test-model','test-project'))
        self.prior=patch.object(paid,'prior_cost',return_value=(5_088_800,90_278_300))
        self.route.start();self.prior.start();self.addCleanup(self.route.stop);self.addCleanup(self.prior.stop)
        self.calls=0

    def fake(self,*args):self.calls+=1;return envelope(self.packet)
    def send(self,fn=None,r=None,d=None):
        return paid.send(self.root,self.benchmark,receipt() if r is None else r,
                         self.packet['packet_digest'] if d is None else d,_transport=fn or self.fake)
    def journal(self):return b.parse((self.benchmark/'bridge-ledger.json').read_bytes())

    def test_dry_request_has_no_tools_or_paths(self):
        value=json.loads(paid.request(self.packet,'model'))
        self.assertEqual(value['tools'],[]);self.assertIs(value['store'],False)
        self.assertNotIn('/Users/',value['input']);self.assertEqual(value['max_output_tokens'],4096)

    def test_success_reserves_before_network_stages_only_and_counts_reasoning_once(self):
        def call(body,key,project):
            self.assertEqual(self.journal()[0]['status'],'unknown')
            self.assertEqual(self.journal()[0]['packet_digest'],self.packet['packet_digest'])
            return self.fake()
        result=self.send(call)
        self.assertEqual(result['estimated_micro_rub'],170_000)
        self.assertEqual(result['combined_estimated_micro_rub'],95_537_100)
        with b.session(self.root) as state:self.assertEqual(state['version'],1);self.assertEqual(state['phase'],'proposed')
        self.assertEqual(self.journal()[0]['status'],'settled')
        with self.assertRaises(b.BridgeError):self.send()
        self.assertEqual(self.calls,1)

    def test_fresh_coverage_and_reviewed_digest_fail_before_reservation(self):
        bad=[]
        for key,value in [('observed_at',(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat()),
                          ('ai_studio_covered',False),('personal_funds_allowed',True),('grant_remaining_rub',1),
                          ('output_rub_per_1000',0.6)]:
            r=receipt();r[key]=value;bad.append(r)
        for r in bad:
            with self.assertRaises(b.BridgeError):self.send(r=r)
        with self.assertRaises(b.BridgeError):self.send(d='0'*64)
        self.assertFalse((self.benchmark/'bridge-ledger.json').exists());self.assertEqual(self.calls,0)

    def test_cap_counts_original_benchmark(self):
        with patch.object(paid,'prior_cost',return_value=(19_000_000,90_278_300)):
            with self.assertRaisesRegex(b.BridgeError,'20_rub'):self.send()
        self.assertEqual(self.calls,0)

    def test_timeout_unknown_usage_and_keyboard_interrupt_hold_reserve_no_retry(self):
        def unknown(*args):self.calls+=1;raise TimeoutError('sensitive text must never escape')
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome_stop'):self.send(unknown)
        row=self.journal()[0];self.assertEqual(row['status'],'unknown');self.assertNotIn('estimated_micro_rub',row)
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome_stop'):self.send()
        self.assertEqual(self.calls,1)

    def test_keyboard_interrupt_holds_reserve_and_releases_locks(self):
        def interrupted(*args):raise KeyboardInterrupt()
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome'):self.send(interrupted)
        self.assertEqual(self.journal()[0]['status'],'unknown')
        with b.session(self.root) as state:self.assertEqual(state['version'],1)
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome'):self.send()

    def test_credential_read_failure_reserves_and_never_sends(self):
        self.key.unlink()
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome'):self.send()
        self.assertEqual(self.journal()[0]['status'],'unknown');self.assertEqual(self.calls,0)

    def test_changed_response_binding_is_paid_but_not_staged(self):
        def changed(*args):
            packet=copy.deepcopy(self.packet);packet['request_id']='0'*32
            return envelope(packet)
        with self.assertRaisesRegex(b.BridgeError,'binding'):self.send(changed)
        self.assertEqual(self.journal()[0]['status'],'settled')
        with b.session(self.root) as state:self.assertEqual(state['version'],1);self.assertEqual(state['phase'],'prepared')

    def test_missing_usage_holds_reserve(self):
        def no_usage(*args):response=envelope(self.packet);response.pop('usage');return response
        with self.assertRaisesRegex(b.BridgeError,'unknown_outcome'):self.send(no_usage)
        self.assertEqual(self.journal()[0]['status'],'unknown')

    def test_incomplete_is_paid_but_never_staged_or_retried(self):
        def incomplete(*args):response=envelope(self.packet);response['status']='incomplete';return response
        with self.assertRaisesRegex(b.BridgeError,'incomplete'):self.send(incomplete)
        self.assertEqual(self.journal()[0]['status'],'settled')
        with b.session(self.root) as state:self.assertEqual(state['phase'],'prepared')
        with self.assertRaisesRegex(b.BridgeError,'no_retry'):self.send()

    def test_invalid_usage_and_over_reservation_hold_reserve(self):
        for usage in ({'input_tokens':True,'output_tokens':3},{'input_tokens':3,'output_tokens':4097},
                      {'input_tokens':3,'output_tokens':3,'output_tokens_details':{'reasoning_tokens':4}}):
            with self.assertRaises(b.BridgeError):paid.usage_cost(usage)
        def excess(*args):response=envelope(self.packet);response['usage']['input_tokens']=100_000;return response
        with self.assertRaises(b.BridgeError):self.send(excess)
        self.assertEqual(self.journal()[0]['status'],'unknown')

    def test_physical_session_and_budget_slots_prevent_parallel_calls(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def slow(*args):entered.set();release.wait(5);return envelope(self.packet)
        def first():
            try:self.send(slow)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=first);thread.start();self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(b.BridgeError,'session_busy'):self.send()
            with self.assertRaisesRegex(b.BridgeError,'session_busy'):b.cancel(self.root)
            other=b.create('event');self.addCleanup(shutil.rmtree,other)
            with b.session(other) as state:other_digest=state['packet']['packet_digest']
            with self.assertRaisesRegex(b.BridgeError,'paid_request_busy'):
                paid.send(other,self.benchmark,receipt(),other_digest,_transport=self.fake)
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

    def test_corrupt_negative_journal_fails_closed(self):
        b.atomic(self.benchmark/'bridge-ledger.json',[dict(status='settled',case_id='event',estimated_micro_rub=-1,reserved_micro_rub=1)])
        with self.assertRaisesRegex(b.BridgeError,'invalid_budget'):self.send()
        self.assertEqual(self.calls,0)

    def test_real_route_is_hash_and_original_location_pinned(self):
        self.route.stop()
        (self.benchmark/'run.mjs').write_text('untrusted route')
        with self.assertRaises(b.BridgeError):paid.route(self.benchmark)


if __name__ == '__main__':unittest.main()
