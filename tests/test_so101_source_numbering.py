"""Numbering is metadata only; old scientific bytes and control AST stay fixed."""
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import uuid

import pytest

from explorer import skill_catalogue as catalogue
from explorer import lab_skill_workflow as flow
from explorer.store import Store
from explorer_shared.contracts import canonical,digest,event
from explorer_shared.experiments import experiment_id,experiment_identity
from explorer_shared.so101_station import SO101Station,so101_source_identity


SOURCE_ID=experiment_id('so101_source_learning')


@pytest.mark.parametrize('project,version,verified',[(SOURCE_ID,2,True),(SOURCE_ID,1,False),(SOURCE_ID,None,False),('06',1,True),('06',2,False),('06',None,False),('personal',None,True),('personal',1,True),('personal',2,True),('03',2,False),('06',True,False)])
def test_source_role_requires_explicit_epoch_for_numeric_IDs_and_personal_alias_keeps_unknown_epoch(project,version,verified):
    result=so101_source_identity(project,numbering_version=version)
    assert result['verified']==verified and result['original_project_id']==project and result['original_numbering_version']==version
    assert result['project_id']==SOURCE_ID and result['experiment_numbering_version']==2


def test_new_record_default_and_explicit_legacy_request_are_canonical_without_old_file_mutation(tmp_path):
    from test_station_recording_provenance import station_fixture,finish
    station=station_fixture(tmp_path);old=station.root/'recordings/legacy/manifest.json';old.parent.mkdir(parents=True);raw=b'{"project_id":"06","schema_version":1,"state":"complete"}\n';old.write_bytes(raw)
    result=station._start_record({});summary,saved=finish(station)
    for value in (result,summary,saved,saved['source_context']):
        assert value['project_id']==SOURCE_ID and value['experiment_slug']=='so101_source_learning' and value['experiment_numbering_version']==2
    with pytest.raises(ValueError,match='explicit numbering v1'):station._start_record({'project_id':'06'})
    station._start_record({'project_id':'06','experiment_numbering_version':1});_,saved=finish(station)
    assert saved['project_id']==SOURCE_ID and saved['experiment_numbering_version']==2
    assert saved['recording_request_identity']['original_project_id']=='06' and saved['recording_request_identity']['original_numbering_version']==1
    assert old.read_bytes()==raw


def test_source_identity_uses_verified_job_projection_or_server_epoch_never_an_ambiguous_page(tmp_path):
    raw={'project_id':'06','episode_id':'legacy'};before=canonical(raw)
    job={'project_id':SOURCE_ID,'config':experiment_identity('so101_source_learning')}
    assert not flow._source_identity(job,raw,'a'*64)['verified']
    projected=deepcopy(job);projected['numbering_provenance']=dict(source='verified_migration_index',projection_sha256='b'*64,index_sha256='c'*64,original_project_id='06',original_numbering_version=1)
    assert flow._source_identity(projected,raw,'a'*64)['verified'] and flow._job_identity(projected)['verified']
    server=deepcopy(job);server['config']['source_numbering_version']=1
    assert flow._source_identity(server,raw,'a'*64)['verified']
    wrong=deepcopy(projected);wrong['numbering_provenance']['original_project_id']='04'
    assert not flow._job_identity(wrong)['verified']
    assert canonical(raw)==before


def test_ACT_worker_legacy_scope_is_explicit_and_current_transfer06_is_never_SO101():
    from explorer_sim.so101_skill_training import _source_job_identity
    assert _source_job_identity({'project_id':SOURCE_ID,'config':experiment_identity('so101_source_learning')})['verified']
    assert not _source_job_identity({'project_id':'06','config':{}})['verified']
    assert not _source_job_identity({'project_id':'06','config':{'experiment_numbering_version':2}})['verified']
    assert _source_job_identity({'project_id':'06','config':{'experiment_numbering_version':1}})['verified']
    assert not _source_job_identity({'project_id':SOURCE_ID,'config':{'experiment_numbering_version':1}})['verified']


def test_legacy_declared_Spec_read_confirmation_and_revision_preserve_original_body_and_SHA(tmp_path):
    store=Store(tmp_path/'state');legacy=catalogue.propose_skill(template_id='pick_object_to_container');legacy.update(owner_experiment='06',experiment_numbering_version=1)
    original=canonical(legacy);payload=dict(type='skill.catalogue.spec',spec=legacy);envelope=event('nx','06',payload);envelope['event_id']='skill.catalogue.spec:'+legacy['skill_id']+':1';envelope['experiment_numbering_version']=1;raw=canonical(envelope).decode()
    with store.connect() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(envelope['event_id'],'nx',1,envelope['sha256'],raw))
    confirmed=catalogue.confirm_proposal(store,legacy['skill_id'],1,digest(legacy))
    assert canonical(confirmed['spec'])==original and confirmed['spec_sha256']==digest(legacy) and confirmed['canonical_identity']['project_id']==SOURCE_ID
    assert catalogue.is_source_spec(legacy)
    ambiguous=deepcopy(legacy);ambiguous.pop('experiment_numbering_version')
    assert not catalogue.is_source_spec(ambiguous)
    revised=catalogue.revise_proposal(store,legacy['skill_id'],1,{'title':'New canonical version'},digest(legacy))
    assert revised['spec']['owner_experiment']==SOURCE_ID and revised['spec']['experiment_numbering_version']==2 and revised['spec']['version']==2
    with store.connect() as db:assert db.execute('SELECT body FROM events WHERE event_id=?',(envelope['event_id'],)).fetchone()['body']==raw


def test_API_projects_legacy_source_Spec_for_display_but_validates_original_immutable_SHA(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from explorer.lab_skill_api import install_router
    store=Store(tmp_path/'state');legacy=catalogue.propose_skill(template_id='pick_object_to_container');legacy.update(owner_experiment='06',experiment_numbering_version=1)
    original_spec=canonical(legacy);checksum=digest(legacy)
    payload=dict(type='skill.catalogue.spec',spec=legacy);envelope=event('nx','06',payload);envelope.update(event_id='skill.catalogue.spec:'+legacy['skill_id']+':1',experiment_numbering_version=1)
    raw=canonical(envelope).decode()
    with store.connect() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(envelope['event_id'],'nx',1,envelope['sha256'],raw))
    app=FastAPI();install_router(app,lambda:store);client=TestClient(app)
    route='/api/lab/skills/'+legacy['skill_id'];view=client.get(route+'?version=1').json()
    assert view['spec']['owner_experiment']==SOURCE_ID and view['spec']['experiment_numbering_version']==2
    assert view['spec_sha256']==checksum and view['spec_sha256_basis']=='original_immutable_spec'
    assert view['canonical_spec_sha256']==digest(view['spec']) and view['canonical_spec_sha256']!=checksum
    assert canonical(view['legacy_provenance']['original_spec'])==original_spec
    assert client.post(route+'/confirm',json=dict(version=1,spec_sha256=view['canonical_spec_sha256'])).status_code==400
    confirmed=client.post(route+'/confirm',json=dict(version=1,spec_sha256=checksum))
    assert confirmed.status_code==200 and confirmed.json()['confirmed'] and confirmed.json()['spec']['owner_experiment']==SOURCE_ID
    with store.connect() as db:assert db.execute('SELECT body FROM events WHERE event_id=?',(envelope['event_id'],)).fetchone()['body']==raw


def _without_recording_identity_metadata(method):
    """Normalize only the later, explicitly verified stale-finish metadata fence.

    Keep the historical numbering proof untouched. Any other change in these
    methods, including motor calls, locking, sampling or limits, still fails its
    original AST digest below.
    """
    method=deepcopy(method)
    if method.name=='_work':
        targets=[node for node in ast.walk(method) if isinstance(node,ast.If) and ast.dump(node.test)==ast.dump(ast.parse('target is not None',mode='eval').body)]
        assert len(targets)==1
        target=targets[0];assert len(target.body)==2
        assert ast.dump(target.body[0])==ast.dump(ast.parse("publication_gate=getattr(self,'publication_gate',None)").body[0])
        gate=target.body[1];assert isinstance(gate,ast.If)
        assert ast.dump(gate.test)==ast.dump(ast.parse('publication_gate is not None and publication_gate(self.control_epoch,self.mode,target) is not True',mode='eval').body)
        denied=ast.parse('self._end_session()\nself._write(follower)\npublication_gate.measured_hold_returned(self.control_epoch,follower,measured_at,time.monotonic_ns())').body
        assert [ast.dump(node) for node in gate.body]==[ast.dump(node) for node in denied]
        # Only this exact publication-permission wrapper is removed; the whole
        # original accepted/write/receipt/sample body still matches its old SHA.
        accepted,admission=gate.orelse
        assert ast.dump(accepted)==ast.dump(ast.parse('accepted = [min(HIGH[i],max(LOW[i],float(value))) for i,value in enumerate(target)]').body[0])
        assert isinstance(admission,ast.If)
        assert ast.dump(admission.test)==ast.dump(ast.parse('self._write(accepted,observed_context=(self.control_epoch,self.mode,target)) is False',mode='eval').body)
        assert [ast.dump(node) for node in admission.body]==[ast.dump(node) for node in denied]
        target.body=[accepted,ast.parse('self._write(accepted)').body[0]]+admission.orelse
    if method.name=='_write':
        assert len(method.args.kwonlyargs)==1 and method.args.kwonlyargs[0].arg=='observed_context'
        assert ast.dump(method.args.kw_defaults[0])==ast.dump(ast.Constant(value=None))
        method.args.kwonlyargs=[];method.args.kw_defaults=[]
        assert len(method.body)==1 and isinstance(method.body[0],ast.With)
        body=method.body[0].body
        wrapper=ast.parse("publication_gate=getattr(self,'publication_gate',None)\nif observed_context is not None and publication_gate is not None:\n    values=dict(zip(NAMES,target))\n    return publication_gate.publish(observed_context,lambda:self.buses['follower'].sync_write('Goal_Position',values))").body
        assert [ast.dump(node) for node in body[:2]]==[ast.dump(node) for node in wrapper]
        method.body[0].body=body[2:]
    if method.name=='status':
        fields=[(call,keyword) for call in ast.walk(method) if isinstance(call,ast.Call)
                for keyword in call.keywords if keyword.arg=='recording_identity_fence']
        assert len(fields)==1
        call,keyword=fields[0]
        assert isinstance(call.func,ast.Name) and call.func.id=='dict'
        assert ast.dump(keyword.value)==ast.dump(ast.Constant(value=True))
        call.keywords.remove(keyword)
    if method.name in ('command','_finish_record'):
        callee='_finish_record' if method.name=='command' else '_finish_record_locked'
        calls=[call for call in ast.walk(method) if isinstance(call,ast.Call)
               and isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name)
               and call.func.value.id=='self' and call.func.attr==callee]
        assert len(calls)==1
        keywords=[keyword for keyword in calls[0].keywords if keyword.arg=='expected_episode_id']
        assert len(keywords)==1
        expression="body.get('episode_id',body.get('recording_id'))" if method.name=='command' else 'expected_episode_id'
        assert ast.dump(keywords[0].value)==ast.dump(ast.parse(expression,mode='eval').body)
        calls[0].keywords.remove(keywords[0])
    if method.name=='_finish_record':
        assert len(method.args.kwonlyargs)==len(method.args.kw_defaults)==1
        assert method.args.kwonlyargs[0].arg=='expected_episode_id'
        assert method.args.kwonlyargs[0].annotation is None
        assert ast.dump(method.args.kw_defaults[0])==ast.dump(ast.Constant(value=None))
        method.args.kwonlyargs=[]
        method.args.kw_defaults=[]
    return method


def test_all_original_station_control_and_sampling_method_ASTs_unchanged_by_metadata_migration():
    proof=json.loads((Path(__file__).parents[1]/'research/audits/SO101_NUMBERING_CONTROL_INVARIANCE_20261026.json').read_text())
    tree=ast.parse(Path(inspect.getsourcefile(SO101Station)).read_text());station=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='SO101Station')
    methods={node.name:hashlib.sha256(ast.dump(_without_recording_identity_metadata(node),include_attributes=False).encode()).hexdigest() for node in station.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    for name,expected in proof['unchanged_station_methods_AST_sha256'].items():assert methods[name]==expected,name
    assert proof['allowed_changed_station_methods']==['_finish_record_locked','_start_record']
    constants={node.targets[0].id:hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
               for node in tree.body if isinstance(node,ast.Assign) and len(node.targets)==1
               and isinstance(node.targets[0],ast.Name)}
    for name,expected in proof['unchanged_coordinate_constants_AST_sha256'].items():assert constants[name]==expected,name


def test_native6_implementation_binding_is_exact_resume_metadata_not_retroactive_package_claims():
    root=Path(__file__).parents[1];proof=json.loads((root/'shared/training/DIFFUSION_NATIVE6_IMPLEMENTATION_BINDING.json').read_text())
    metadata=proof['resume_metadata'];signature=hashlib.sha256(json.dumps(metadata,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert signature==proof['resume_signature'] and proof['completed_steps']==8 and proof['training_updates_executed_during_extraction']==0
    assert metadata['implementation_sha256']['policy'] in proof['official_implementation_sha256'].values()
    assert any(path.endswith('/scheduling_ddim.py') for path in proof['official_implementation_sha256'])
    assert proof['unmeasured_native6_source_dependencies']==['lerobot/policies/diffusion/configuration_diffusion.py','lerobot/optim/optimizers.py']
    assert proof['unmeasured_native6_package_RECORDs'] and not proof['physical_io']
