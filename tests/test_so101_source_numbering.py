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
    original=canonical(legacy);payload=dict(type='skill.catalogue.spec',spec=legacy);envelope=event('nx','06',payload);envelope['event_id']='skill.catalogue.spec:'+legacy['skill_id']+':1';raw=canonical(envelope).decode()
    with store.connect() as db:db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(envelope['event_id'],'nx',1,envelope['sha256'],raw))
    confirmed=catalogue.confirm_proposal(store,legacy['skill_id'],1,digest(legacy))
    assert canonical(confirmed['spec'])==original and confirmed['spec_sha256']==digest(legacy) and confirmed['canonical_identity']['project_id']==SOURCE_ID
    assert catalogue.is_source_spec(legacy)
    ambiguous=deepcopy(legacy);ambiguous.pop('experiment_numbering_version')
    assert not catalogue.is_source_spec(ambiguous)
    revised=catalogue.revise_proposal(store,legacy['skill_id'],1,{'title':'New canonical version'},digest(legacy))
    assert revised['spec']['owner_experiment']==SOURCE_ID and revised['spec']['experiment_numbering_version']==2 and revised['spec']['version']==2
    with store.connect() as db:assert db.execute('SELECT body FROM events WHERE event_id=?',(envelope['event_id'],)).fetchone()['body']==raw


def test_all_original_station_control_and_sampling_method_ASTs_unchanged_by_metadata_migration():
    proof=json.loads((Path(__file__).parents[1]/'research/audits/SO101_NUMBERING_CONTROL_INVARIANCE_20261026.json').read_text())
    tree=ast.parse(Path(inspect.getsourcefile(SO101Station)).read_text());station=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='SO101Station')
    methods={node.name:hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest() for node in station.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    for name,expected in proof['unchanged_station_methods_AST_sha256'].items():assert methods[name]==expected,name
    assert proof['allowed_changed_station_methods']==['_finish_record_locked','_start_record']
