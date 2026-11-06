"""Recorded-observation checks never acquire a camera or motor bus."""
import json
from types import SimpleNamespace

import pytest

from so101_lab import inference
from so101_lab.features import FeatureContract, sha256_file


def test_latency_check_uses_fresh_segmentation_and_reports_slow_pipeline(monkeypatch):
    clocks = iter([0., .1, .4, 1., 1.3, 3.3])
    monkeypatch.setattr(inference.time, 'monotonic', lambda: next(clocks))
    annotations = []
    class Segmenter:
        def annotate_observation(self, frame, images, trackers):
            result = {'fresh': len(annotations)}
            annotations.append(result)
            return result
    received = []
    loaded = SimpleNamespace(predict=lambda frame, images, ann, task: received.append(ann) or [0.] * 6)
    result = inference.benchmark_observation(loaded, {}, {}, 'task', Segmenter())
    assert result['status'] == 'LATENCY_EXCEEDS_LIMIT'
    assert result['observation_age_limit_ms'] == 2000
    assert result['samples'][-1]['total_ms'] == pytest.approx(2300)
    assert received == annotations and len(received) == 2
    assert result['physical_actions'] is False


def test_stopped_check_does_not_predict():
    loaded = SimpleNamespace(predict=lambda *a: pytest.fail('Cancelled model check'))
    result = inference.benchmark_observation(loaded, {}, {}, '', stop=lambda: True)
    assert result['status'] == 'stopped' and result['samples'] == []


def test_checkpoint_check_verifies_raw_files_and_never_opens_hardware(tmp_path, monkeypatch):
    from test_training import fixture_dataset
    from so101_lab import resources, hardware
    data = fixture_dataset(tmp_path / 'data/ds_fixture')
    checkpoint = tmp_path / 'models/step';folder = checkpoint / 'pretrained_model';folder.mkdir(parents=True)
    raw = data / 'episodes/ep000/frames.jsonl'
    rows=[json.loads(line) for line in raw.read_text().splitlines()]
    for row in rows:row['state_valid']=True  # Contract-valid fixture only; still synthetic source, no physical evidence.
    rows[0]['state_valid']=False  # Match training: invalid observations are excluded, not used for prediction.
    raw.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    lineage = {'dataset_path': str(data), 'split': {'validation': ['ep000'], 'train': []},
               'dataset_hashes': {'episodes/ep000/frames.jsonl': sha256_file(raw)}}
    (folder / 'lineage.json').write_text(json.dumps(lineage))
    calls = []
    loaded = SimpleNamespace(checkpoint=checkpoint, contract=FeatureContract(mode='rgb_state'),
                             predict=lambda *args: calls.append(args) or [0.] * 6)
    monkeypatch.setattr(resources, 'ROOT', tmp_path)
    monkeypatch.setattr(inference, 'load_checkpoint', lambda *a, **kw: loaded)
    monkeypatch.setattr(hardware, 'HardwareSource', lambda *a, **kw: pytest.fail('No hardware in model check'))
    result = inference.check_checkpoint(checkpoint, {'device': 'cpu'})
    assert result['loaded'] and result['latency_check']['status'] == 'PASS'
    assert len(calls) == 2
    assert result['sample_frame_id']==rows[1]['frame_id'] and result['rejected_observations_before_sample']==1
    assert not result['physical_actions']
    raw.write_text(raw.read_text() + '\n')
    with pytest.raises(ValueError, match='изменился'):
        inference.check_checkpoint(checkpoint, {'device': 'cpu'})


def test_checkpoint_without_raw_remains_loadable_and_does_not_claim_forward(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'models/step';folder = checkpoint / 'pretrained_model';folder.mkdir(parents=True)
    (folder / 'lineage.json').write_text(json.dumps({'dataset_path': str(tmp_path / 'missing')}))
    loaded = SimpleNamespace(checkpoint=checkpoint, contract=FeatureContract())
    from so101_lab import resources
    monkeypatch.setattr(resources, 'ROOT', tmp_path)
    monkeypatch.setattr(inference, 'load_checkpoint', lambda *a, **kw: loaded)
    result = inference.check_checkpoint(checkpoint, {'device': 'cpu'})
    assert result['loaded']
    assert result['latency_check']['status'] == 'NOT_TESTED'


def test_physical_start_rejects_known_slow_checkpoint_before_preview_or_hardware(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from so101_lab import app as web, resources
    folder=tmp_path/'models/step/pretrained_model';folder.mkdir(parents=True)
    (folder/'config.json').write_text('{}')
    manifest=folder.parent/'checkpoint.json';manifest.write_text('{}')
    monkeypatch.setattr(web,'ROOT',tmp_path)
    monkeypatch.setattr(web,'teleoperation',None)
    monkeypatch.setattr(web,'manager',SimpleNamespace(status=lambda:{'active':False}))
    monkeypatch.setattr(web,'camera_preview',SimpleNamespace(stop=lambda:pytest.fail('Preview must stay open')))
    monkeypatch.setattr(resources,'assert_hardware_available',lambda *a:pytest.fail('No device check after known slow result'))
    monkeypatch.setattr(web.jobs,'list_jobs',lambda:[{'kind':'policy_load','state':'completed','dataset':str(folder),
        'result':{'checkpoint_manifest_sha256':sha256_file(manifest),'latency_check':{'latency_within_limit':False}}}])
    response=TestClient(web.app).post('/api/policy/start',json={'checkpoint':str(folder),'task':'task'})
    assert response.status_code==409
    assert '2 секунды' in response.json()['detail']
