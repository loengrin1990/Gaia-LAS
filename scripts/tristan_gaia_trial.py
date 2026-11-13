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

from scripts import tristan_synthetic as b
from scripts import tristan_synthetic_review as review_ui

PREFIX = 'gaia-tristan-workspace-'


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
            self.packet=self.record['packet'];self.candidate=b.reply(self.packet,self.record['response'])
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
        b.validate_packet(self.packet)
        for ref,source in zip(self.record['refs'],self.packet['documents']):
            view=self.desk.preview(ref['id'])
            b.require(view['digest']==ref['digest'] and view['text']==source['text'],'trial_sources_changed')
        b.require(len(self.record['refs'])==len(self.packet['documents']),'trial_sources_changed')
        memory=self.desk._memory_state()
        b.require(len(memory['history'])==memory['version'] and memory['history'][-1]['artifact']==memory['current'],'invalid_trial_history')
        requests=[r for r in memory['requests'] if r.get('mode')=='addressed_v1']
        b.require(len(requests)<=1 and len(memory['history']) in (1,2),'invalid_trial_history')
        b.require(self.desk._memory_text(memory['history'][0]['artifact'])==self.packet['before'],'trial_memory_changed')
        r=requests[0] if requests else None
        if r:
            b.require(r['base_version']==self.packet['base_version'] and r['materials']==self.record['refs'],'local_binding_mismatch')
            for key in ['previous','task','evidence','packet']:
                text=self.pilot._read(r[key]);b.require(hashlib.sha256(text.encode()).hexdigest()==r[key+'_digest'],'local_packet_changed')
            b.require(self.pilot._read(r['previous'])==self.packet['before'] and self.pilot._read(r['task'])==self.packet['task']
                and json.loads(self.pilot._read(r['evidence']))==[d['text'] for d in self.packet['documents']],'local_binding_mismatch')
            if r.get('operations'):b.require(self.pilot._operations(r)==self.candidate['operations'],'candidate_changed')
            binding=dict(remote_request_id=self.packet['request_id'],remote_packet_digest=self.packet['packet_digest'],
                         base_version=self.packet['base_version'],local_request_id=r['id'],local_packet_digest=r['packet_digest'])
            if (self.root/'binding.json').exists():b.require(b.parse((self.root/'binding.json').read_bytes())==binding,'local_binding_mismatch')
            if r['status']=='accepted':
                expected=b.apply_operations(self.packet['before'],[self.candidate['operations'][i] for i in r['accepted_selection']],
                                            [d['text'] for d in self.packet['documents']])[0]
                b.require(memory['version']==2 and len(memory['history'])==2 and self.desk._memory_text(memory['current'])==expected,'trial_memory_changed')
            else:b.require(memory['version']==1 and self.desk._memory_text(memory['current'])==self.packet['before'],'stale_version')
        else:b.require(memory['version']==1 and self.desk._memory_text(memory['current'])==self.packet['before'],'stale_version')
        return memory,r

    def state(self):
        with self.lock:
            memory,r=self._request()
            statuses={'addressed_prepared':'prepared','addressed_proposed':'proposed','accepted':'confirmed',
                      'cancelled':'cancelled','addressed_no_changes':'no_changes'}
            phase=statuses.get(r['status'],'unavailable') if r else 'ready'
            b.require(phase!='unavailable','invalid_trial')
            value=dict(phase=phase,version=memory['version'],mode='gaia_trial',origin=self.record['mode'],workspace=True,error=None,
                       before=self.packet['before'],memory=self.desk._memory_text(memory['current']),task=self.packet['task'],
                       sources=self.packet['documents'],operations=self.candidate['operations'] if r and r.get('operations') else [],
                       accepted_indices=r.get('accepted_selection',[]) if r else [])
            value['view_digest']=b.digest(dict(view=value,remote=self.packet['packet_digest'],local=r['id'] if r else None))
            return value

    def workspace(self):
        with self.lock:
            state=self.state()
            history=self.desk.working_memory()['history']
            return dict(state=state,project=self.desk.project,materials=self.packet['documents'],
                        history=[dict(version=h['version'],text=h['text']) for h in history])

    def action(self,action,body):
        schemas={'prepare':{'view_digest'},'cancel':{'view_digest'},'preview':{'view_digest','indices'},
                 'confirm':{'view_digest','indices','preview_token','confirm'}}
        b.require(action in schemas and isinstance(body,dict) and set(body)==schemas[action],'invalid_request')
        with self.lock:
            try:
                current=self.state();b.require(body['view_digest']==current['view_digest'],'preview_changed')
                _,r=self._request()
                if action=='prepare':
                    if r and r['status']!='addressed_prepared':return current
                    if not r:
                        self.pilot.prepare(dict(version=1,task=self.packet['task'],selected=self.record['refs']))
                        _,r=self._request()
                    binding=dict(remote_request_id=self.packet['request_id'],remote_packet_digest=self.packet['packet_digest'],
                        base_version=self.packet['base_version'],local_request_id=r['id'],local_packet_digest=r['packet_digest'])
                    b.atomic(self.root/'binding.json',binding)
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
