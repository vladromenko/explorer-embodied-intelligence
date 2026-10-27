"""Fake source/actor only: admission, causal boundary, padding and baselines."""
from dataclasses import replace
from copy import deepcopy
import io
import json
from pathlib import Path

import numpy as np
import pytest

from explorer_shared.contracts import ACTION_NAMES, ACTION_UNITS, canonical, digest
from explorer_shared.learning.sim_object_route import canonical_teacher, LOW, HIGH
from explorer_shared.learning.sim_train_forward_audit import (
    AuditRejected, TrainSource, load_train_source, actor_packet, target_labels,
    scorer_chunk, baselines, Metrics, audit_source, _npz)
from test_sim_object_route import fixture as original_fixture, plan as original_plan

RATE = dict(velocity_deg_s=[48,48,48,60,60,72], period_s=.05)


def source_fixture(tmp_path):
    case,result,rows,obs,images,score=original_fixture()
    result.update(eligible_as_simulation_expert=True,physical_io=False,skill_id='skill',target_skill_version=3)
    envelope,payloads,report,admission=canonical_teacher(result,rows,obs,images,case,'skill',3,score)
    folder=tmp_path/'episodes'/case['id']
    for name,value in payloads.items():
        p=folder/'canonical'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
    for name,value in [('canonical/envelope.json',envelope),('canonical/admission.json',report),
                       ('raw-teacher/result.json',result),('raw-teacher/target-trace.json',rows)]:
        p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(canonical(value))
    from PIL import Image
    rgb=np.asarray(Image.open(io.BytesIO(next(iter(images.values())))).resize((8,8)))
    with (folder/'view.npz').open('wb') as stream:
        np.savez_compressed(stream,rgb=np.repeat(rgb[None],len(rows),axis=0),
                            state_native=np.asarray([r['preceding_command9'] for r in rows],np.float32),
                            action_native=np.asarray([r['actual_dispatched_action9'] for r in rows],np.float32),
                            frame_monotonic_ns=np.asarray([r['renderer_receipt']['stamp']['monotonic_ns'] for r in rows],np.int64))
    prefix='episodes/'+case['id']+'/'
    descriptor=dict(id=case['id'],source_episode_id=case['id'],split='train',frames=len(rows),admission=admission,
                    scene_id=case['scene_id'],session_id=case['session_id'],geometry_signature=case['geometry_signature'],
                    canonical_sample_correspondence_verified=True,file=prefix+'view.npz',
                    sha256=__import__('hashlib').sha256((folder/'view.npz').read_bytes()).hexdigest(),
                    canonical_envelope_file=prefix+'canonical/envelope.json',canonical_envelope_sha256=digest(envelope),
                    canonical_admission_file=prefix+'canonical/admission.json',canonical_admission_sha256=digest(report),
                    raw_target_result_sha256=digest(result),raw_target_trace_sha256=digest(rows))
    manifest=dict(schema='explorer_fresh_target_visual_dataset_v1',action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),
                  skill_id='skill',skill_version=3,source_kind='simulation',split_plan=original_plan(),episodes=[descriptor])
    p=tmp_path/'manifest.json';p.write_bytes(canonical(manifest))
    kwargs=dict(episode_id=case['id'],skill_id='skill',skill_version=3,count=len(rows),image_size=8)
    return p,manifest,kwargs


def small_source(n=19):
    prior=np.repeat([[90,90,90,90,90,30,0,0,0]],n,axis=0).astype(np.float32)
    actions=prior.copy();actions[:,0]+=1;prior[1:]=actions[:-1]
    return TrainSource(np.full((n,8,8,3),107,np.uint8),prior,actions,np.arange(n,dtype=np.int64)*50000000,
                       tuple('close' if i<n//2 else 'release' for i in range(n)),{})


def test_source_admission_uses_actual_canonical_report_rgb_cast_and_causal_labels(tmp_path):
    p,manifest,kwargs=source_fixture(tmp_path)
    data=load_train_source(p,digest(manifest),**kwargs)
    assert len(data.rgb)==9 and not data.rgb.flags.writeable and data.phases[2]=='close'
    assert len(data.provenance['source_files_sha256'])>9
    assert data.prior9.dtype==data.action9.dtype==np.float32
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(AuditRejected,match='SHA'):load_train_source(p,digest(manifest),**kwargs)


@pytest.mark.parametrize('mutation',['development','unknown','duplicate','path','false_expert','wrong_scene'])
def test_source_denial_precedes_any_actor_or_model_access(tmp_path,mutation):
    p,m,kwargs=source_fixture(tmp_path);d=m['episodes'][0]
    if mutation=='development':d['split']='development'
    elif mutation=='unknown':d['admission']['complete_verified_teacher']=False
    elif mutation=='duplicate':m['episodes'].append(deepcopy(d))
    elif mutation=='path':d['canonical_admission_file']='../outside.json'
    elif mutation=='wrong_scene':d['scene_id']='renamed-unverified-scene'
    else:
        q=p.parent/d['canonical_admission_file'];r=json.loads(q.read_bytes());r['eligible_as_successful_bc_expert']=False;q.write_bytes(canonical(r));d['canonical_admission_sha256']=digest(r)
    p.write_bytes(canonical(m))
    with pytest.raises(AuditRejected):load_train_source(p,digest(m),**kwargs)


def test_npz_declared_giant_shape_is_rejected_before_numpy_array_allocation(monkeypatch):
    import zipfile
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name in ['rgb','state_native','action_native','frame_monotonic_ns']:
            stream=io.BytesIO();np.lib.format.write_array_header_1_0(stream,dict(descr='|u1',fortran_order=False,shape=(2**40,)))
            z.writestr(name+'.npy',stream.getvalue())
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('allocation parser called'))
    with pytest.raises(AuditRejected,match='shape/dtype'):_npz(output.getvalue(),9,8)


@pytest.mark.parametrize('cell',['ZERO_ABS','ELAPSED_ABS','ZERO_INCREMENT','ELAPSED_INCREMENT'])
def test_fake_queries_have_only_identical_causal_actor_packets_when_future_labels_change(cell):
    source=small_source();calls=[];retained=[]
    def predict(actor):
        assert set(vars(actor))=={'rgb','state10'} and actor.state10.shape==(10,)
        calls.append((actor.rgb.tobytes(),actor.state10.tobytes()))
        return np.zeros((16,9),np.float32)
    first=audit_source(source,cell,RATE,40,predict,lambda *x:retained.append(x[0]),image_size=8)
    original=calls.copy();calls.clear()
    changed=replace(source,action9=source.action9.copy(),phases=tuple('settle' for _ in source.phases))
    # Change a future label with no subsequent prior observation to invalidate.
    changed.action9[-1,0]+=.25
    audit_source(changed,cell,RATE,40,predict,lambda *x:None,image_size=8)
    assert calls==original and retained==list(range(19)) and first['queries']==19
    assert first['metrics']['MODEL_PRIOR_ZERO']==first['metrics']['ZERO_NORMALIZED']


def test_padding_denominator_horizon_phase_and_joint_error_are_not_diluted():
    source=small_source(9);labels=np.zeros((9,9),np.float32);metric=Metrics('ZERO_ABS',RATE)
    for origin in range(9):
        target=scorer_chunk(source,labels,origin);target['labels'][target['padded']]=1e8
        metric.add(np.ones((16,9),np.float32),target)
    r=metric.result()['groups']
    assert r['all']['valid_action_slots']==45 and r['all']['valid_scalar_labels']==405
    assert r['horizon/0..7']['valid_action_slots']==44 and r['horizon/8..15']['valid_action_slots']==1
    assert r['all']['normalized_MAE']==1 and r['all']['native_label_MAE_by_joint'][5]==70
    assert r['target_phase/close']['valid_action_slots']+r['target_phase/release']['valid_action_slots']==45


def test_causal_hold_baseline_and_labels_distinguish_absolute_endpoint_from_increment():
    source=small_source();abs_labels=target_labels(source,'ZERO_ABS',RATE);inc_labels=target_labels(source,'ZERO_INCREMENT',RATE)
    np.testing.assert_array_equal(abs_labels,((source.action9-LOW)*2/(HIGH-LOW)-1).astype(np.float32))
    np.testing.assert_allclose(inc_labels[:,0],(source.action9[:,0]-source.prior9[:,0])/2.4,rtol=0,atol=1e-7)
    hold=baselines(source,0,'ZERO_ABS')['HOLD_ORIGIN_NATIVE9'].copy()
    source.prior9[1:]+=1
    np.testing.assert_array_equal(baselines(source,0,'ZERO_ABS')['HOLD_ORIGIN_NATIVE9'],hold)
    assert baselines(source,0,'ZERO_INCREMENT')['HOLD_ORIGIN_NATIVE9'][0,5]==0
    assert hold[0,5]==-1 and baselines(source,0,'ZERO_ABS')['ZERO_NORMALIZED'][0,5]==0


def test_unknown_nonfinite_or_truncated_predictions_stop_without_fabricated_completion():
    source=small_source();calls=[]
    for value in [np.zeros((8,9),np.float32),np.full((16,9),np.nan,np.float32),np.zeros((16,9),float)]:
        with pytest.raises(AuditRejected,match='float32 full16'):
            audit_source(source,'ZERO_ABS',RATE,40,lambda actor:value,lambda *x:calls.append(x),image_size=8)
    assert calls==[]
    source.ticks[2]=source.ticks[1]
    with pytest.raises(AuditRejected,match='ordered'):
        audit_source(source,'ZERO_ABS',RATE,40,lambda actor:pytest.fail('actor called'),lambda *x:None,image_size=8)
