"""Opt-in synthetic workspace on real ContextDesk persistence. No sender/runtime hook."""
import argparse
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import uuid
import re

from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_review as review_ui

PREFIX = 'gaia-tristan-workspace-'


def followup(before, request_id):
    """Closed OFFLINE teaching fixture at v2; never an imported model reply."""
    b.require(isinstance(request_id,str) and re.fullmatch('[0-9a-f]{32}',request_id),'invalid_request_id')
    old=next((text for text in ('Инструкция проверки ещё не подготовлена.','Инструкция проверки подготовлена.') if text in before),None)
    b.require(old is not None and 'Работа разрешена после проверки пользовательских прав.' in before,'unsupported_followup')
    instruction='Инструкция проверки подготовлена и опубликована.'
    rights='Проверка пользовательских прав завершена.'
    text=rights+' '+instruction+' Координатор продолжает собирать замечания. Проверка прав остаётся обязательным условием работы.'
    operations=[dict(action='replace',old_text=old,new_text=instruction,quote=instruction)]
    pending='Проверка пользовательских прав ещё не выполнена.'
    operations.append(dict(action='replace' if pending in before else 'append',old_text=pending if pending in before else '',new_text=rights,quote=rights))
    packet=dict(protocol='gaia-tristan-offline-followup-v1',case_id='partial_followup',request_id=request_id,base_version=2,
                before=before,documents=[dict(name='Новое письмо — проверка прав.txt',text=text)],
                task='Обновить готовность инструкции и результат проверки прав по новому письму. Сохранить обязанности и условие работы.')
    packet['packet_digest']=b.digest(packet)
    b.apply_operations(before,operations,[text])
    return dict(packet=packet,response=review_ui.envelope(packet,operations),mode='offline_followup')


def desk_at(root):
    # Never load the user's Gaia config, profile or provider settings.
    config = root/'runtime.json'
    paths = {key:str(root/'runtime'/key) for key in ['vault','projects','service_docs','runs','exports','storage',
             'obsidian_work','transcriber','lm_studio_launcher','transcriber_launcher']}
    expected = dict(config_version=1,paths=paths,server=dict(host='127.0.0.1',port=8788),
                    endpoints=dict(lm_studio='http://127.0.0.1:9/v1/chat/completions'))
    if config.exists(): b.require(b.parse(config.read_bytes())==expected,'invalid_trial_config')
    else:b.atomic(config,expected)
    previous=os.environ.get('GAIA_CONFIG');os.environ['GAIA_CONFIG']=str(config)
    try:
        from gaia.context_desk import ContextDesk
    finally:
        if previous is None:os.environ.pop('GAIA_CONFIG',None)
        else:os.environ['GAIA_CONFIG']=previous
    return ContextDesk(dict(project='Синтетическая проба Gaia',memory_root=str(root/'memory')),root/'data')


def create(source=None, kind='partial'):
    if source:
        with b.session(source) as state:
            packet,response=copy.deepcopy(state['packet']),copy.deepcopy(state['response'])
        b.reply(packet,response);mode='recorded_replay'
    else:
        demo=review_ui.demo(kind)
        b.require(demo.root is not None,'unresolved_fixture_conflict')
        with b.session(demo.root) as state:packet,response=copy.deepcopy(state['packet']),copy.deepcopy(state['response'])
        b.reply(packet,response);mode='offline_demo'
    root=Path(tempfile.mkdtemp(prefix=PREFIX));(root/'memory').mkdir()
    desk=desk_at(root)
    def upload(name,text):return desk.upload(dict(name=name,data=base64.b64encode(text.encode()).decode()))
    seed=upload('Основа.txt',packet['before'])
    request=desk.prepare_memory(dict(version=0,task='Начальная синтетическая основа',selected=[seed]))
    desk.propose_memory(dict(id=request['id'],text=packet['before']))
    desk.decide_memory(dict(id=request['id'],decision='accept'))
    refs=[]
    for number,row in enumerate(packet['documents'],1):
        view=upload(row['name'],row['text']);refs.append(dict(id=view['id'],digest=view['digest'],number=number))
    # Persist only controlled fixture values and opaque local provenance refs.
    b.atomic(root/'trial.json',dict(protocol='gaia-tristan-workspace-v1',packet=packet,response=response,mode=mode,refs=refs))
    return Trial(root)


class Trial:
    def __init__(self,root):
        root=Path(root)
        b.require(root.is_absolute() and root.parent.resolve()==Path(tempfile.gettempdir()).resolve()
                  and root.name.startswith(PREFIX) and root.is_dir() and not root.is_symlink(),'isolated_trial_required')
        b.require(not any(path.is_symlink() for path in root.rglob('*')),'invalid_trial_files')
        self.file=(root/'trial.lock').open('a')
        try:
            fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.root=root;self.record=b.parse((root/'trial.json').read_bytes())
            b.require(self.record.get('protocol')=='gaia-tristan-workspace-v1','invalid_trial')
            self.first_packet=self.record['packet'];self.first_candidate=b.reply(self.first_packet,self.record['response'])
            self.packet=self.first_packet;self.candidate=self.first_candidate
            self.desk=desk_at(root)
            from gaia.addressed_memory import AddressedMemoryPilot
            self.pilot=AddressedMemoryPilot(self.desk);self.lock=self.desk.lock;self.ticket=None
            self.state()
        except BaseException:
            self.file.close();raise

    def close(self):
        self.file.close()

    def _request(self):
        b.require(b.parse((self.root/'trial.json').read_bytes())==self.record,'trial_changed')
        b.require(b.parse(self.desk.state_path.read_bytes())==self.desk.state,'stale_version')
        b.validate_packet(self.first_packet)
        memory=self.desk._memory_state()
        b.require(type(memory['version'])is int and 1<=len(memory['history'])<=3 and len(memory['history'])==memory['version'] and memory['history'][-1]['artifact']==memory['current'],'invalid_trial_history')
        requests=[r for r in memory['requests'] if r.get('mode')=='addressed_v1']
        records=[self.record]
        next_path=self.root/'next.json'
        if next_path.exists():
            b.require(self.first_packet['case_id']=='partial' and requests and requests[0]['status']=='accepted' and len(memory['history'])>=2,'unsupported_followup')
            base=self.desk._memory_text(memory['history'][1]['artifact'])
            saved=b.parse(next_path.read_bytes())
            b.require(saved['packet']['request_id']!=self.first_packet['request_id'],'reply_binding_mismatch')
            expected=followup(base,saved['packet']['request_id'])
            b.require(set(saved)=={'packet','response','mode','refs'} and all(saved[k]==expected[k] for k in expected),'followup_changed')
            records.append(saved)
        b.require(len(requests)<=len(records)<=2,'invalid_trial_history')
        expected_text=self.first_packet['before'];version=1;self.summaries=[]
        b.require(self.desk._memory_text(memory['history'][0]['artifact'])==expected_text,'trial_memory_changed')
        for index,record in enumerate(records):
            packet=record['packet']
            candidate=self.first_candidate if index==0 else b.parse(record['response']['output'][0]['content'][0]['text'].encode())
            b.require(type(packet['base_version'])is int and packet['base_version']==version and packet['before']==expected_text,'stale_version')
            b.require(len(record['refs'])==len(packet['documents']),'trial_sources_changed')
            for ref,source in zip(record['refs'],packet['documents']):
                view=self.desk.preview(ref['id'])
                b.require(view['digest']==ref['digest'] and view['text']==source['text'],'trial_sources_changed')
            r=requests[index] if index<len(requests) else None
            if r:
                b.require(r['base_version']==version and r['materials']==record['refs'],'local_binding_mismatch')
                for key in ['previous','task','evidence','packet']:
                    text=self.pilot._read(r[key]);b.require(hashlib.sha256(text.encode()).hexdigest()==r[key+'_digest'],'local_packet_changed')
                b.require(self.pilot._read(r['previous'])==expected_text and self.pilot._read(r['task'])==packet['task']
                    and json.loads(self.pilot._read(r['evidence']))==[d['text'] for d in packet['documents']],'local_binding_mismatch')
                if r.get('operations'):b.require(self.pilot._operations(r)==candidate['operations'],'candidate_changed')
                binding=dict(remote_request_id=packet['request_id'],remote_packet_digest=packet['packet_digest'],
                             base_version=packet['base_version'],local_request_id=r['id'],local_packet_digest=r['packet_digest'])
                binding_path=self.root/('binding.json' if index==0 else 'next-binding.json')
                if binding_path.exists():b.require(b.parse(binding_path.read_bytes())==binding,'local_binding_mismatch')
                if r['status']=='accepted':
                    expected_text=b.apply_operations(expected_text,[candidate['operations'][i] for i in r['accepted_selection']],
                                                     [d['text'] for d in packet['documents']])[0]
                    version+=1
                    b.require(len(memory['history'])>=version and memory['history'][version-1]['request']==r['id']
                        and self.desk._memory_text(memory['history'][version-1]['artifact'])==expected_text,'trial_memory_changed')
                elif index<len(records)-1:b.require(False,'invalid_trial_history')
            self.summaries.append(dict(number=index+1,base_version=packet['base_version'],status=r['status'] if r else 'ready',
                                       accepted_count=len(r.get('accepted_selection',[])) if r else 0))
            if index==len(records)-1:
                self.packet=packet;self.candidate=candidate;self.active_record=record;self.round=index+1;active=r
        b.require(memory['version']==version and len(memory['history'])==version
                  and self.desk._memory_text(memory['current'])==expected_text,'stale_version')
        return memory,active

    def state(self):
        with self.lock:
            memory,r=self._request()
            statuses={'addressed_prepared':'prepared','addressed_proposed':'proposed','accepted':'confirmed',
                      'cancelled':'cancelled','addressed_no_changes':'no_changes'}
            phase=statuses.get(r['status'],'unavailable') if r else 'ready'
            b.require(phase!='unavailable','invalid_trial')
            value=dict(phase=phase,version=memory['version'],mode='gaia_trial',origin=self.active_record['mode'],workspace=True,error=None,
                       round_number=self.round,can_advance=False,
                       before=self.packet['before'],memory=self.desk._memory_text(memory['current']),task=self.packet['task'],
                       sources=self.packet['documents'],operations=self.candidate['operations'] if r and r.get('operations') else [],
                       accepted_indices=r.get('accepted_selection',[]) if r else [])
            if self.round==1 and phase=='confirmed' and self.first_packet['case_id']=='partial':
                try:followup(value['memory'],'0'*32);value['can_advance']=True
                except ValueError:pass
            value['view_digest']=b.digest(dict(view=value,remote=self.packet['packet_digest'],local=r['id'] if r else None))
            return value

    def workspace(self):
        with self.lock:
            state=self.state()
            history=self.desk.working_memory()['history']
            return dict(state=state,project=self.desk.project,materials=self.packet['documents'],
                        history=[dict(version=h['version'],text=h['text']) for h in history],passes=self.summaries)

    def action(self,action,body):
        schemas={'advance':{'view_digest'},'prepare':{'view_digest'},'cancel':{'view_digest'},'preview':{'view_digest','indices'},
                 'confirm':{'view_digest','indices','preview_token','confirm'}}
        b.require(action in schemas and isinstance(body,dict) and set(body)==schemas[action],'invalid_request')
        with self.lock:
            try:
                current=self.state();b.require(body['view_digest']==current['view_digest'],'preview_changed')
                _,r=self._request()
                if action=='advance':
                    b.require(current['can_advance'] and self.round==1,'sequence_closed')
                    record=followup(current['memory'],uuid.uuid4().hex)
                    refs=[]
                    for number,source in enumerate(record['packet']['documents'],1):
                        uploaded=self.desk.upload(dict(name=source['name'],data=base64.b64encode(source['text'].encode()).decode()))
                        refs.append(dict(id=uploaded['id'],digest=uploaded['digest'],number=number))
                    record['refs']=refs
                    b.atomic(self.root/'next.json',record);self.ticket=None
                    return self.state()
                if action=='prepare':
                    if r and r['status']!='addressed_prepared':return current
                    if not r:
                        self.pilot.prepare(dict(version=self.packet['base_version'],task=self.packet['task'],selected=self.active_record['refs']))
                        _,r=self._request()
                    binding=dict(remote_request_id=self.packet['request_id'],remote_packet_digest=self.packet['packet_digest'],
                        base_version=self.packet['base_version'],local_request_id=r['id'],local_packet_digest=r['packet_digest'])
                    b.atomic(self.root/('binding.json' if self.round==1 else 'next-binding.json'),binding)
                    # Original remote binding is checked above. Translation is limited to this exact local packet.
                    self.pilot.propose(dict(id=r['id'],reply=dict(request_id=r['id'],base_version=r['base_version'],
                                                               operations=self.candidate['operations'])))
                    return self.state()
                b.require(r is not None,'candidate_required')
                if action=='cancel':
                    self.pilot.decide(dict(id=r['id'],decision='cancel'));self.ticket=None;return self.state()
                b.require(isinstance(body['indices'],list) and all(type(i)is int for i in body['indices']),'invalid_selection')
                if action=='preview':
                    result=self.pilot.selection(dict(id=r['id'],selected=body['indices']))
                    token=secrets.token_urlsafe(24);self.ticket=dict(token=token,view=current['view_digest'],result=result)
                    return dict(preview_token=token,before=result['before'],after=result['after'],indices=result['selected'],selected_count=len(result['selected']))
                ticket=self.ticket
                b.require(body['confirm'] is True and ticket is not None and isinstance(body['preview_token'],str)
                    and secrets.compare_digest(body['preview_token'],ticket['token']) and body['indices']==ticket['result']['selected']
                    and ticket['view']==current['view_digest'],'preview_changed')
                self.pilot.decide(dict(id=r['id'],selected=ticket['result']['selected'],digest=ticket['result']['digest'],decision='accept'))
                self.ticket=None;return self.state()
            except Exception:self.ticket=None;raise


def main():
    p=argparse.ArgumentParser(description='Opt-in SYNTHETIC Gaia workspace. No model calls or production profile.')
    source=p.add_mutually_exclusive_group();source.add_argument('--resume',type=Path);source.add_argument('--replay-session',type=Path)
    p.add_argument('--demo',choices=['partial','unsupported'],default='partial');p.add_argument('--port',type=int,default=0)
    args=p.parse_args()
    try:
        trial=Trial(args.resume) if args.resume else create(args.replay_session,args.demo)
        server=review_ui.make_server(trial,args.port,workspace=True)
    except (ValueError,OSError,KeyError,TypeError):
        print('Синтетическое пространство недоступно. Рабочая Gaia не изменена.');raise SystemExit(2)
    print(f'Синтетическое пространство Gaia: http://127.0.0.1:{server.server_port}/workspace',flush=True)
    print(f'Для локального продолжения после остановки: --resume {trial.root}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();trial.close()

if __name__=='__main__':main()
