"""Проверки оптимизаций обучения: кэш хэшей, подвыборка validation, retention checkpoint, пакетная доставка MLflow."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import torch

from so101_lab import training, telemetry
from so101_lab.training_options import normalize_options, disk_estimate


def test_new_options_validated():
    o = normalize_options({'policy_type': 'act', 'amp_dtype': 'fp16', 'use_amp': True, 'num_workers': 4, 'keep_last_checkpoints': 3, 'eval_max_samples': 0}, public=True)
    assert o['amp_dtype'] == 'fp16' and o['num_workers'] == 4 and o['keep_last_checkpoints'] == 3
    for bad in ({'amp_dtype': 'int8'}, {'num_workers': 5}, {'keep_last_checkpoints': -1}, {'eval_max_samples': -1}):
        with pytest.raises(ValueError):
            normalize_options(bad, public=True)
    assert normalize_options({}, public=True)['amp_dtype'] == 'bf16'
    assert normalize_options({'use_amp': True}, public=True)['amp_dtype'] == 'bf16'
    with pytest.raises(ValueError, match='bf16'):  # раньше это падало внутри GradScaler уже во время обучения
        normalize_options({'policy_type': 'smolvla', 'use_amp': True, 'amp_dtype': 'fp16'}, public=True)


def test_disk_estimate_counts_retention_and_skips_tiny():
    base = normalize_options({'steps': 10000, 'save_every': 500}, public=True)
    all_kept = disk_estimate(base)
    pruned = disk_estimate(normalize_options({'steps': 10000, 'save_every': 500, 'keep_last_checkpoints': 2}, public=True))
    assert pruned['need_bytes'] < all_kept['need_bytes'] / 5
    assert disk_estimate(normalize_options({'tiny_model': True, 'device': 'cpu'})) is None


def test_hash_cache_skips_rehash_until_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(training, 'ROOT', tmp_path)
    episode = tmp_path / 'data' / 'ds_x' / 'episodes' / 'e1'
    episode.mkdir(parents=True)
    image = episode / 'a.png'
    image.write_bytes(b'raw-pixels')
    digest = training.sha256_file(image)
    calls = []
    real = training.sha256_file
    monkeypatch.setattr(training, 'sha256_file', lambda p: (calls.append(p), real(p))[1])

    cache = training._HashCache(episode)
    assert cache.verify(image, digest, episode.resolve()) and len(calls) == 1
    cache.save()
    second = training._HashCache(episode)
    assert second.verify(image, digest, episode.resolve()) and len(calls) == 1  # из кэша

    image.write_bytes(b'tampered-with-different-length')  # другой размер → запись недействительна (одинаковый размер+mtime в один тик ядра ловить нельзя)
    third = training._HashCache(episode)
    assert not third.verify(image, digest, episode.resolve()) and len(calls) == 2
    assert not training._HashCache(episode).verify(image, 'f' * 64, episode.resolve())


def test_hash_cache_never_written_outside_laboratory_data(tmp_path):
    episode = tmp_path / 'elsewhere' / 'e1'
    episode.mkdir(parents=True)
    image = episode / 'a.png'
    image.write_bytes(b'x')
    cache = training._HashCache(episode)
    assert cache.file is None
    assert cache.verify(image, training.sha256_file(image), episode.resolve())
    cache.save()
    assert not (training.ROOT / 'cache/hash-verified' / 'nonexistent').exists()


def _checkpoints(root, steps):
    for step in steps:
        folder = root / 'checkpoints' / f'step_{step:08d}'
        folder.mkdir(parents=True)
        (folder / 'checkpoint.json').write_text('{}')
    (root / 'checkpoints' / '.step_00000099.partial').mkdir()


def test_prune_checkpoints_keeps_latest_resume_source_and_partials(tmp_path):
    _checkpoints(tmp_path, [100, 200, 300, 400])
    assert training._prune_checkpoints(tmp_path, 0) == []
    resume = tmp_path / 'checkpoints' / 'step_00000100'
    removed = training._prune_checkpoints(tmp_path, 2, protect=[resume])
    assert removed == ['step_00000200']
    left = sorted(p.name for p in (tmp_path / 'checkpoints').iterdir())
    assert left == ['.step_00000099.partial', 'step_00000100', 'step_00000300', 'step_00000400']


class _Policy:
    training = True
    config = type('C', (), {'device': 'cpu'})()
    def __init__(self):
        self.seen = []
    def eval(self):
        self.training = False
    def train(self, mode=True):
        self.training = mode
    def __call__(self, batch):
        self.seen.extend(batch['id'].tolist())
        return batch['id'].float().mean(), {}


class _Dataset:
    def __getitem__(self, i):
        return {'id': torch.tensor(i), 'task': 'x'}


def test_evaluate_subsample_is_deterministic_uniform_and_thread_equal():
    indices = list(range(1000))
    first, second = _Policy(), _Policy()
    loss_a = training.evaluate(first, lambda b: b, _Dataset(), indices, 8, max_samples=100)
    loss_b = training.evaluate(second, lambda b: b, _Dataset(), indices, 8, max_samples=100)
    assert loss_a == loss_b and len(first.seen) == 100 and first.seen == second.seen
    assert first.seen[0] == 0 and first.seen[-1] >= 980  # равномерно по всему диапазону, а не первые 100
    full, pooled = _Policy(), _Policy()
    with ThreadPoolExecutor(3) as pool:
        loss_full = training.evaluate(full, lambda b: b, _Dataset(), indices, 8)
        loss_pool = training.evaluate(pooled, lambda b: b, _Dataset(), indices, 8, pool=pool)
    assert len(full.seen) == 1000 and full.seen == pooled.seen and loss_full == loss_pool
    assert full.training is True  # режим train восстановлен


def test_mlflow_metrics_are_delivered_in_batches(tmp_path, monkeypatch):
    import mlflow.tracking
    batches = []
    class FakeRun:
        info = type('I', (), {'run_id': 'r1', 'experiment_id': '1'})()
    class FakeClient:
        def __init__(self, *a, **k): pass
        def get_experiment_by_name(self, name): return type('E', (), {'experiment_id': '1'})()
        def create_run(self, *a, **k): return FakeRun()
        def log_batch(self, run_id, metrics=(), params=()): batches.append((len(list(metrics)), len(list(params))))
        def log_artifact(self, *a, **k): pass
        def set_terminated(self, *a, **k): batches.append('closed')
    monkeypatch.setattr(mlflow.tracking, 'MlflowClient', FakeClient)
    monkeypatch.setattr('mlflow.set_tracking_uri', lambda uri: None)
    monkeypatch.delenv('SO101_MLFLOW_OFFLINE', raising=False)
    monkeypatch.delenv('SO101_MLFLOW_SPOOL', raising=False)
    log = telemetry.DurableMLflow(tmp_path, 'run', {'a': 1}, delivery_thread=False)
    for step in range(1, 201):
        log.log_metrics({'loss': 1.0 / step, 'lr': 1e-4, 'grad': 0.1}, step=step)
    log.close('FINISHED')
    metric_batches = [b for b in batches if b != 'closed' and b[0]]
    assert sum(b[0] for b in metric_batches) == 600
    assert len(metric_batches) <= 2  # а не 200 запросов
    assert batches[-1] == 'closed'
    assert not list(log.path.glob('*.json')) and len(list(log.path.glob('*.sent'))) == 202  # params + 200 metrics + status


def test_artifact_upload_timeout_scales_with_size_and_is_restored(tmp_path, monkeypatch):
    monkeypatch.setenv('MLFLOW_HTTP_REQUEST_TIMEOUT', '3')
    small = tmp_path / 'small.json'
    small.write_text('{}')
    with telemetry._upload_timeout(small):
        import os
        assert int(os.environ['MLFLOW_HTTP_REQUEST_TIMEOUT']) >= 30  # 3 с обрывало бы даже умеренные файлы
    big = tmp_path / 'ckpt'
    big.mkdir()
    with (big / 'model.bin').open('wb') as f:
        f.truncate(2 * 1024 ** 3)  # sparse 2 ГБ
    with telemetry._upload_timeout(big):
        assert 100 < int(os.environ['MLFLOW_HTTP_REQUEST_TIMEOUT']) <= 1800
    assert os.environ['MLFLOW_HTTP_REQUEST_TIMEOUT'] == '3'


def test_offline_mode_keeps_events_in_spool_and_never_touches_network(tmp_path, monkeypatch):
    monkeypatch.setenv('SO101_MLFLOW_OFFLINE', '1')
    monkeypatch.setenv('SO101_MLFLOW_SPOOL', str(tmp_path / 'spool'))
    log = telemetry.DurableMLflow(tmp_path / 'root', 'offline', {'a': 1}, delivery_thread=False)
    log.log_metrics({'loss': 1.0}, step=1)
    log.close('FINISHED')  # без сервера на :5020 не должно быть ни ошибок, ни доставки
    assert log.path.parent == tmp_path / 'spool' and not (tmp_path / 'root').exists()
    assert len(list(log.path.glob('*.json'))) == 3 and not list(log.path.glob('*.sent'))  # params + metrics + status
