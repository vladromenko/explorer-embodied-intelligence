"""Temporary bytes only: no model/physics decoder or archived code executes."""
import copy
import io
import json
import subprocess
import sys
from pathlib import Path
import tarfile

import pytest

from explorer.research_export_job import request_settings,run_research_export,ResearchExportBlocked,EXTENSIONS,_hash
from explorer.initial_state_export import SCHEMA,PROFILE,MAX_TOTAL,inspect_recorded_bundle
from explorer_shared.research_export import ExportRejected,LIMITS
from explorer_shared.source_archive import canonical,sha
from test_research_export_job import sources,launch


def bundle(folder):
    root=folder/'initial-state';root.mkdir()
    source=b"raise RuntimeError('EXPLICIT_ARCHIVED_CODE_MUST_NOT_EXECUTE')\n"
    asset=b'EXPLICIT_SOFTWARE_CAD_BYTES_NOT_A_PHYSICAL_CALIBRATION'
    payloads={'compiled-model.mjb':b'EXPLICIT_MJB_BYTE_FIXTURE_NOT_MUJOCO_MODEL',
        'native-integration.npy':b'EXPLICIT_NPY_BYTE_FIXTURE_NOT_NUMPY_ARRAY',
        'profile-state.json':canonical(dict(scope='synthetic_byte_export_test_only')),
        'model.xml':b'<explicit_synthetic_export_fixture/>',
        'source/'+sha(source)+'.bin':source,'assets/'+sha(asset)+'.bin':asset}
    descriptors={name:dict(sha256=sha(raw),bytes=len(raw)) for name,raw in payloads.items()}
    helper=sha(source)
    document=dict(schema=SCHEMA,profile=PROFILE,boundary='after_reset_before_first_observe_or_dispatch',
        physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL',
        initial_RGB_render_reexecution='UNQUALIFIED',finite_command_reexecution='UNQUALIFIED_UNTIL_NEW_PID_TEST',
        source_bindings=dict(initial_capture_helper=dict(file='source/'+helper+'.bin',sha256=helper,bytes=len(source))),
        assets={'fixture.stl':dict(file='assets/'+sha(asset)+'.bin',sha256=sha(asset),bytes=len(asset))},
        runtime=dict(helper_sha256=helper),payloads=descriptors)
    for name,raw in payloads.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    (root/'manifest.json').write_bytes(canonical(document))
    record=dict(status='RECORDED',requested=True,helper_id=PROFILE,helper_sha256=helper,
        manifest_path=str(root/'manifest.json'),manifest_sha256=sha(canonical(document)),payloads=copy.deepcopy(descriptors),
        files=len(payloads)+1,total_payload_bytes=sum(map(len,payloads.values())),physical_io=False,
        restore_reexecution_verified=False,initial_RGB_render_reexecution='UNQUALIFIED',original_runtime_OS_closure='PARTIAL')
    return root,document,record


def reseal(root,document,record):
    (root/'manifest.json').write_bytes(canonical(document));record['manifest_sha256']=sha(canonical(document))
    record['payloads']=copy.deepcopy(document['payloads']);record['files']=len(record['payloads'])+1
    record['total_payload_bytes']=sum(row['bytes'] for row in record['payloads'].values())


def finish(store,source,record,*,artifact_only=False,folder=None):
    if artifact_only:(folder/'zz-target-result.json').write_bytes(canonical(dict(initial_state=record)))
    # Fixture source already terminal: create a fresh terminal source rather
    # than rewriting an immutable completed real job.
    job=store.create_job('software_initial_state_export_qualification','06',{})
    other=store.root/'jobs'/job['id'];other.mkdir()
    pointer=dict(initial_state=record) if not artifact_only else dict(source_manifest_path=str(folder/'zz-target-result.json'),source_manifest_path_sha256=sha((folder/'zz-target-result.json').read_bytes()))
    # Explicit parent identity allows the inherited source job directory.
    store.update_job(job['id'],status='completed',result=dict(pointer,parent_job_id=source['id']))
    return store.job(job['id'])


def archive_payload(result):
    with tarfile.open(result['evidence_tar_path']) as stream:
        return [stream.extractfile(member).read() for member in stream if member.isfile()]


@pytest.mark.parametrize('artifact_only',[False,True])
def test_recorded_bundle_exact_binary_bytes_survive_export_and_independent_restore(tmp_path,artifact_only):
    store,source,folder=sources(tmp_path);root,document,record=bundle(folder)
    job=finish(store,source,record,artifact_only=artifact_only,folder=folder)
    originals={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    settings=request_settings(store,[job['id']]);request=json.loads((store.root/settings['request_manifest']).read_bytes())
    nodes=[node for node in request['registry']['nodes'] if 'format_admission' in node]
    assert len(nodes)==len(document['payloads']) and '.mjb' not in EXTENSIONS and '.bin' not in EXTENSIONS
    for node in nodes:
        admission=node['format_admission'];assert admission['manifest_sha256']==record['manifest_sha256']
        assert node['dependencies']==[admission['admission_node_id']]
        assert not admission['numerical_reexecution_verified_by_export'] and admission['initial_RGB_reexecution']=='UNQUALIFIED'
    export=launch(store,settings);result=run_research_export(store,export,settings);raw=archive_payload(result)
    assert result['verification']=='PARTIAL' and result['independent_restore_verified']
    assert all(value in raw for value in originals.values())
    manifest=json.loads(Path(result['export_manifest_path']).read_bytes())
    assert any((row.get('source_locator') or {}).get('relative_path','').endswith('.mjb') for row in manifest['file_manifest'])
    receipts=[json.loads(value) for value in raw if value.startswith(b'{') and b'explorer_recorded_initial_state_export_admission_v1' in value]
    assert any(value.get('schema')=='explorer_recorded_initial_state_export_admission_v1' and not value['numerical_reexecution_verified_by_export'] for value in receipts)
    assert all(Path(path).read_bytes()==value for path,value in originals.items())
    assert not result['scientific_success_claimed'] and not result['physical_io']


@pytest.mark.parametrize('change',['missing_record','wrong_profile','manifest_sha','member_sha','extra_file','alias','traversal','over_budget','sqlite','secret','missing_capture_helper','helper_sha'])
def test_unqualified_or_resealed_invalid_bundle_never_permits_opaque_binary(tmp_path,change):
    store,source,folder=sources(tmp_path);root,document,record=bundle(folder)
    if change=='missing_record':record['status']='NOT_RECORDED'
    elif change=='wrong_profile':document['profile']='UNREGISTERED_MODEL_PROFILE';reseal(root,document,record)
    elif change=='manifest_sha':record['manifest_sha256']='f'*64
    elif change=='missing_capture_helper':
        document['source_bindings']={'qualification_driver':document['source_bindings']['initial_capture_helper']};reseal(root,document,record)
    elif change=='helper_sha':
        document['runtime']['helper_sha256']='f'*64;reseal(root,document,record)
    elif change=='member_sha':(root/'compiled-model.mjb').write_bytes(b'CHANGED_MJB_BYTES')
    elif change=='extra_file':(root/'extra.bin').write_bytes(b'UNDECLARED_BINARY')
    elif change=='alias':
        outside=tmp_path/'outside.bin';outside.write_bytes((root/'compiled-model.mjb').read_bytes())
        (root/'compiled-model.mjb').unlink();(root/'compiled-model.mjb').symlink_to(outside)
    elif change=='traversal':
        document['payloads']['../escape.bin']=dict(sha256='a'*64,bytes=1);reseal(root,document,record)
    elif change=='over_budget':
        document['payloads']['compiled-model.mjb']['bytes']=MAX_TOTAL+1;reseal(root,document,record)
    elif change=='sqlite':
        raw=b'SQLite format 3\x00PRIVATE_DB_BYTES';(root/'compiled-model.mjb').write_bytes(raw)
        document['payloads']['compiled-model.mjb']=dict(sha256=sha(raw),bytes=len(raw));reseal(root,document,record)
    elif change=='secret':
        (root/'profile-state.json').write_bytes(b'{"node_token":"PRIVATE_NODE_VALUE"}')
        raw=(root/'profile-state.json').read_bytes();document['payloads']['profile-state.json']=dict(sha256=sha(raw),bytes=len(raw));reseal(root,document,record)
    job=finish(store,source,record)
    settings=request_settings(store,[job['id']]);request=json.loads((store.root/settings['request_manifest']).read_bytes())
    assert not any(node.get('format_admission') for node in request['registry']['nodes'])
    assert not any(node.get('relative_path','').endswith(('.mjb','.bin')) for node in request['registry']['nodes'])
    assert request['registry']['missing_dependencies']
    result=run_research_export(store,launch(store,settings),settings)
    assert result['verification']=='PARTIAL'
    raw=b''.join(archive_payload(result))
    assert all(forbidden not in raw for forbidden in (b'EXPLICIT_MJB_BYTE_FIXTURE_NOT_MUJOCO_MODEL',b'UNDECLARED_BINARY',b'PRIVATE_DB_BYTES',b'PRIVATE_NODE_VALUE'))


def test_admitted_binary_mutated_after_enqueue_fails_without_rewriting_original(tmp_path):
    store,source,folder=sources(tmp_path);root,document,record=bundle(folder);job=finish(store,source,record)
    settings=request_settings(store,[job['id']]);export=launch(store,settings)
    path=root/'compiled-model.mjb';path.write_bytes(b'NEW_ORIGINAL_BYTES_RETAINED')
    with pytest.raises(Exception,match='SHA differs'):
        run_research_export(store,export,settings)
    assert path.read_bytes()==b'NEW_ORIGINAL_BYTES_RETAINED'
    assert not (store.root/'jobs'/export['id']/'evidence.tar').exists()


def test_unknown_raw_TAR_binary_is_still_withheld_not_a_generic_extension_exception(tmp_path):
    store,source,folder=sources(tmp_path)
    with tarfile.open(folder/'unknown.tar','w') as stream:
        raw=b'OPAQUE_BINARY_MUST_REMAIN_WITHHELD';item=tarfile.TarInfo('compiled-model.mjb');item.size=len(raw);stream.addfile(item,io.BytesIO(raw))
    settings=request_settings(store,[source['id']]);result=run_research_export(store,launch(store,settings),settings)
    assert result['verification']=='PARTIAL' and b'OPAQUE_BINARY_MUST_REMAIN_WITHHELD' not in b''.join(archive_payload(result))


def test_export_inspector_imports_no_physics_policy_or_array_decoder():
    root=Path(__file__).parents[1]
    code="import sys;sys.path[:0]="+repr([str(root/'shared'),str(root/'00-control-center/src')])+";import explorer.initial_state_export;assert not ({'torch','mujoco','numpy'} & set(sys.modules))"
    outcome=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert outcome.returncode==0,outcome.stdout+outcome.stderr


def test_declared_byte_limits_reject_before_hashing_any_payload(tmp_path):
    root,document,record=bundle(tmp_path)
    document['payloads']['compiled-model.mjb']['bytes']=MAX_TOTAL+1;reseal(root,document,record)
    calls=[]
    def managed(path):
        path=Path(path).absolute();return path,path,'fixture',path.name
    def unexpected_hash(*args,**kwargs):
        calls.append(args);raise AssertionError('Payload hash must follow complete metadata bound preflight')
    with pytest.raises(ExportRejected,match='per-file byte bound'):
        inspect_recorded_bundle(record,base=root,managed_file=managed,hash_file=unexpected_hash,limits=LIMITS)
    assert not calls


@pytest.mark.parametrize('padding',[16*1024**2+1,64*1024-5])
@pytest.mark.parametrize('category',['source','assets'])
def test_source_or_CAD_binary_secret_after_large_prefix_or_across_chunk_boundary_is_withheld(tmp_path,padding,category):
    store,source,folder=sources(tmp_path);root,document,record=bundle(folder)
    raw=b' '*padding+b'NODE_TOKEN=SYNTHETIC_SOURCE_SECRET_MUST_NOT_EXPORT\n'
    name=category+'/'+sha(raw)+'.bin';path=root/name;path.write_bytes(raw)
    entry=dict(file=name,sha256=sha(raw),bytes=len(raw))
    collection='source_bindings' if category=='source' else 'assets'
    document[collection]['extra_source' if category=='source' else 'extra.stl']=entry
    document['payloads'][name]=dict(sha256=sha(raw),bytes=len(raw));reseal(root,document,record)
    def managed(path):
        path=Path(path).absolute();return path,path,'fixture',path.name
    with pytest.raises(ExportRejected,match='byte bound|credential'):
        inspect_recorded_bundle(record,base=root,managed_file=managed,hash_file=_hash,limits=LIMITS)
    job=finish(store,source,record);settings=request_settings(store,[job['id']])
    request=json.loads((store.root/settings['request_manifest']).read_bytes())
    assert not any(node.get('format_admission') for node in request['registry']['nodes'])
    result=run_research_export(store,launch(store,settings),settings)
    assert result['verification']=='PARTIAL'
    assert b'SYNTHETIC_SOURCE_SECRET_MUST_NOT_EXPORT' not in b''.join(archive_payload(result))
    assert path.read_bytes()==raw
