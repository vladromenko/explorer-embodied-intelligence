"""ENGINEERING_GLUE: source-bound TRAIN-only ACT prior-latent audit, no execution.

Actor packets contain only RGB and causal state10. Future labels and annotated
phases belong to a separate scorer. No optimizer, renderer or device imports.
"""
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile

import numpy as np
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES, ACTION_UNITS
from .sim_object_route import LOW, HIGH, PHASES, actor_observation, split_cases, target_split
from .sim_temporal_route import CELLS, BASE_SPEED, increment_targets

SCHEMA = 'explorer_TRAIN_only_prior_latent_forward_audit_v1'
IMAGE_KEY = 'observation.images.overview'


class AuditRejected(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise AuditRejected(message)


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def raw_file(path, expected, limit=64 * 1024**2):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= limit, 'Bounded source file required')
    raw = path.read_bytes()
    require(len(raw) <= limit and sha_bytes(raw) == expected, 'Source bytes/SHA differ')
    return raw


def source_file(root, relative):
    require(isinstance(relative, str), 'Contained source-relative path required')
    p = PurePosixPath(relative)
    require(isinstance(relative, str) and relative and not p.is_absolute()
            and all(part not in ('', '.', '..') for part in relative.split('/')),
            'Contained source-relative path required')
    root = Path(root).resolve()
    path = root.joinpath(*p.parts)
    require(not path.is_symlink() and path.resolve().is_relative_to(root), 'Source path escapes immutable root')
    return path.resolve()


def _npz(raw, count, image_size):
    """Check declared NPY shapes/bytes before allocating arrays, no pickle."""
    specs = {'rgb': ((count, image_size, image_size, 3), np.dtype('uint8')),
             'state_native': ((count, 9), np.dtype('float32')),
             'action_native': ((count, 9), np.dtype('float32')),
             'frame_monotonic_ns': ((count,), np.dtype('int64'))}
    arrays = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [entry.filename for entry in archive.infolist()]
        require(len(names) == len(specs) and set(names) == {key + '.npy' for key in specs}, 'Exact numeric NPZ members required')
        for name, (expected_shape, expected_dtype) in specs.items():
            info = archive.getinfo(name + '.npy')
            require(info.file_size <= 32 * 1024**2, 'NPY allocation exceeds bound')
            payload = archive.read(info)
            stream = io.BytesIO(payload)
            version = np.lib.format.read_magic(stream)
            require(version in ((1, 0), (2, 0)), 'Known bounded NPY header required')
            reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
            shape, fortran, dtype = reader(stream, max_header_size=16384)
            require(shape == expected_shape and dtype == expected_dtype and not fortran,
                    'Stored TRAIN array shape/dtype differs')
            require(len(payload) - stream.tell() == np.prod(shape) * dtype.itemsize, 'NPY declared payload bytes differ')
            arrays[name] = np.load(io.BytesIO(payload), allow_pickle=False)
    return arrays


@dataclass(frozen=True)
class TrainSource:
    rgb: np.ndarray
    prior9: np.ndarray
    action9: np.ndarray
    ticks: np.ndarray
    phases: tuple
    provenance: dict


def load_train_source(manifest_path, expected_sha, *, episode_id, skill_id,
                      skill_version, count=439, image_size=64):
    """Admit one exact successful TRAIN source before a policy can be loaded.

The manifest is externally SHA-pinned. Its canonical expert report, raw teacher
result/trace, all declared envelope payloads and sample-exact RGB/prior/actions
are independently checked. A re-labelled heldout or unknown source is rejected.
    """
    path = Path(manifest_path).resolve()
    manifest = json.loads(raw_file(path, expected_sha))
    require(manifest.get('schema') == 'explorer_fresh_target_visual_dataset_v1'
            and manifest.get('action_names') == list(ACTION_NAMES)
            and manifest.get('action_units') == list(ACTION_UNITS)
            and manifest.get('skill_id') == skill_id and manifest.get('skill_version') == skill_version
            and manifest.get('source_kind') == 'simulation', 'Exact original TRAIN dataset identity required')
    episodes = manifest.get('episodes')
    require(isinstance(episodes, list) and len(episodes) == 1, 'One admitted TRAIN episode required')
    d = episodes[0]
    require(d.get('id') == d.get('source_episode_id') == episode_id and d.get('split') == 'train'
            and d.get('frames') == count and d.get('canonical_sample_correspondence_verified') is True
            and d.get('admission', {}).get('eligible_for_optimizer') is True
            and d['admission'].get('complete_verified_teacher') is True
            and d['admission'].get('partition') == 'train', 'Heldout/unknown/incomplete source denied')
    partitions = {partition for partition, values in manifest.get('split_plan', {}).get('scenarios', {}).items()
                  for case in values if case.get('id') == episode_id}
    require(partitions == {'train'}, 'Original split ownership must be TRAIN only')
    case = next(row for row in split_cases(manifest['split_plan']) if row['id'] == episode_id)
    require(all(d.get(key) == case[key] for key in ('scene_id', 'session_id', 'geometry_signature')),
            'Original scene/session/geometry binding differs')
    bound = {str(path): expected_sha}

    def retained(relative, checksum, limit=64 * 1024**2):
        source = source_file(path.parent, relative)
        value = raw_file(source, checksum, limit)
        bound[str(source)] = checksum
        return value

    envelope = json.loads(retained(d['canonical_envelope_file'], d['canonical_envelope_sha256']))
    report = json.loads(retained(d['canonical_admission_file'], d['canonical_admission_sha256']))
    require(report.get('episode_id') == episode_id and report.get('eligible_as_successful_bc_expert') is True
            and report.get('optimizer_partition_allowed') is True
            and report.get('split', {}).get('partition') == 'train'
            and all(report['split'].get(key) == value for key,value in target_split(case).items()), 'Canonical successful TRAIN expert admission required')
    require(envelope.get('episode_id') == episode_id and envelope.get('split', {}).get('partition') == 'train', 'Canonical source identity/split differs')
    canonical_root = source_file(path.parent, d['canonical_envelope_file']).parent
    payloads = {}
    total = 0
    for name, checksum in envelope['source_files_sha256'].items():
        source = source_file(canonical_root, name)
        value = raw_file(source, checksum)
        total += len(value)
        require(total <= 128 * 1024**2, 'Canonical payload budget exceeded')
        payloads[name] = value
        bound[str(source)] = checksum
    from explorer_shared.embodied_episode import inspect_embodied_episode
    fresh = inspect_embodied_episode(envelope, payloads)
    require(fresh.get('eligible_as_successful_bc_expert') is True and fresh.get('optimizer_partition_allowed') is True
            and fresh.get('episode_id') == episode_id and fresh.get('split', {}).get('partition') == 'train', 'Fresh canonical admission denied')
    episode_root = source_file(path.parent, d['file']).parent
    result_path = source_file(episode_root, 'raw-teacher/result.json')
    trace_path = source_file(episode_root, 'raw-teacher/target-trace.json')
    result_raw = raw_file(result_path, d['raw_target_result_sha256'])
    trace_raw = raw_file(trace_path, d['raw_target_trace_sha256'])
    bound[str(result_path)] = sha_bytes(result_raw)
    bound[str(trace_path)] = sha_bytes(trace_raw)
    result, rows = json.loads(result_raw), json.loads(trace_raw)
    require(result.get('failure') is None and result.get('eligible_as_simulation_expert') is True
            and result.get('full_target_simulation_success') is True
            and result.get('optimizer_partition_allowed') is True and result.get('physical_io') is False
            and result.get('target_split') == target_split(case)
            and result.get('skill_id') == skill_id and result.get('target_skill_version') == skill_version
            and result.get('target_trace_sha256') == sha_bytes(trace_raw)
            and result.get('frames') == len(rows) == count, 'Successful immutable raw TRAIN trace required')
    arrays = _npz(retained(d['file'], d['sha256']), count, image_size)
    prior, actual, ticks = arrays['state_native'], arrays['action_native'], arrays['frame_monotonic_ns']
    require(np.isfinite(prior).all() and np.isfinite(actual).all()
            and np.all(np.diff(ticks) > 0) and np.array_equal(prior[1:], actual[:-1]), 'Causal literal TRAIN prior/actions required')
    phases = []
    for i, row in enumerate(rows):
        require(row['index'] == i and row['phase_id'] in PHASES
                and row.get('learned_target_actor_executed') is False
                and row['renderer_receipt']['stamp']['monotonic_ns'] == int(ticks[i])
                and row['dispatch']['pre_action_stamp'] == row['renderer_receipt']['stamp']
                and np.array_equal(np.asarray(row['preceding_command9'], np.float32), prior[i])
                and np.array_equal(np.asarray(row['actual_dispatched_action9'], np.float32), actual[i])
                and row['dispatch']['dispatched_action9'] == row['actual_dispatched_action9'], 'Stored view/raw sample correspondence differs')
        value = payloads.get(row['target_rgb_file'])
        require(value is not None and sha_bytes(value) == row['target_rgb_sha256'], 'Raw TRAIN RGB binding differs')
        image = Image.open(io.BytesIO(value))
        require(image.mode == 'RGB' and image.width * image.height <= 1024**2, 'Bounded true RGB required')
        image.load()
        resized, _ = actor_observation(np.asarray(image), prior[i], image_size)
        require(np.array_equal(resized, arrays['rgb'][i]), 'Stored RGB differs from actual training resize')
        phases.append(row['phase_id'])
    for value in arrays.values():
        value.setflags(write=False)
    return TrainSource(arrays['rgb'], prior, actual, ticks, tuple(phases),
                       dict(episode_id=episode_id,manifest_sha256=expected_sha,source_files_sha256=bound,
                            original_raw_native_values_cast_to_stored_float32=True,source_kind='simulation'))


@dataclass(frozen=True)
class ActorPacket:
    rgb: np.ndarray
    state10: np.ndarray


def actor_packet(rgb, prior9, tick, first_tick, *, cell, horizon_s, image_size):
    require(cell in CELLS and type(tick) is int and type(first_tick) is int
            and 0 <= first_tick <= tick and 0 < horizon_s <= 1000, 'Actual causal TRAIN clock/cell required')
    elapsed = (tick - first_tick) / 1e9
    require(elapsed <= horizon_s, 'TRAIN observation exceeds declared clock horizon')
    image, state = actor_observation(rgb, prior9, image_size)
    clock = np.float32(elapsed / horizon_s) if CELLS[cell]['clock'] == 'ELAPSED' else np.float32(0)
    state10 = np.r_[state, clock].astype(np.float32)
    image.setflags(write=False)
    state10.setflags(write=False)
    return ActorPacket(image, state10)


def target_labels(source, cell, rate):
    require(cell in CELLS, 'Registered cell required')
    if CELLS[cell]['labels'] == 'ABSOLUTE9':
        labels = ((source.action9 - LOW) * 2 / (HIGH - LOW) - 1).astype(np.float32)
    else:
        labels = increment_targets(source.prior9, source.action9,
                                   velocity6=rate['velocity_deg_s'], period_s=rate['period_s'])
    return labels


def scorer_chunk(source, labels, origin, *, chunk_size=16):
    require(type(origin) is int and 0 <= origin < len(source.rgb) and chunk_size == 16, 'Exact source origin/full16 chunk required')
    positions = origin + np.arange(chunk_size)
    padded = positions >= len(labels)
    positions = np.minimum(positions, len(labels) - 1)
    return dict(labels=labels[positions].copy(), padded=padded,
                target_phases=tuple(source.phases[i] for i in positions), origin_phase=source.phases[origin])


def baselines(source, origin, cell):
    """Causal origin-only HOLD; normalized ZERO has representation-specific meaning."""
    prior = source.prior9[origin]
    hold = ((prior - LOW) * 2 / (HIGH - LOW) - 1).astype(np.float32) if CELLS[cell]['labels'] == 'ABSOLUTE9' else np.r_[np.zeros(6), prior[6:] / BASE_SPEED].astype(np.float32)
    return {'ZERO_NORMALIZED': np.zeros((16, 9), np.float32),
            'HOLD_ORIGIN_NATIVE9': np.repeat(hold[None], 16, axis=0)}


class Metrics:
    """Exact masked L1 denominator, without mixed-unit native scalar averages."""
    def __init__(self, cell, rate):
        self.cell = cell
        self.scale = ((HIGH - LOW) / 2).astype(float) if CELLS[cell]['labels'] == 'ABSOLUTE9' else np.r_[np.asarray(rate['velocity_deg_s']) * rate['period_s'], BASE_SPEED]
        self.groups = {}

    def add(self, prediction, target):
        prediction = np.asarray(prediction)
        require(prediction.shape == (16, 9) and np.isfinite(prediction).all(), 'Finite full16x9 prediction required')
        for h in range(16):
            if not target['padded'][h]:
                error = np.abs(prediction[h].astype(float) - target['labels'][h].astype(float))
                motion = float(target['labels'][h, 5])
                groups = ['all', 'horizon/' + ('0..7' if h < 8 else '8..15'), 'horizon/' + str(h),
                          'origin_phase/' + target['origin_phase'], 'target_phase/' + target['target_phases'][h]]
                if CELLS[self.cell]['labels'] == 'DISPATCHED_INCREMENT9':
                    groups.append('target_gripper_delta/' + ('positive' if motion > 0 else 'negative' if motion < 0 else 'zero'))
                for key in groups:
                    row = self.groups.setdefault(key, [0, np.zeros(9), np.zeros(9), np.zeros(9)])
                    row[0] += 1
                    row[1] += error
                    row[2] += error**2
                    row[3] = np.maximum(row[3], error)

    def result(self):
        out = {}
        for key, (n, total, squares, maximum) in sorted(self.groups.items()):
            out[key] = dict(valid_action_slots=n, valid_scalar_labels=n * 9,
                            normalized_MAE=float(total.sum() / (n * 9)),
                            normalized_MAE_by_joint=(total / n).tolist(), normalized_RMSE_by_joint=np.sqrt(squares / n).tolist(),
                            normalized_max_abs_by_joint=maximum.tolist(), native_label_MAE_by_joint=(total / n * self.scale).tolist())
        return dict(schema=SCHEMA, cell=self.cell, groups=out, joint_names=list(ACTION_NAMES),
                    native_label_units=['deg' if i < 6 else ACTION_UNITS[i] for i in range(9)],
                    native_arm_quantity='endpoint' if CELLS[self.cell]['labels'] == 'ABSOLUTE9' else 'dispatched_increment',
                    native_units_not_averaged_across_joints=True, padding_excluded=True,
                    physical_success_evaluated=False)


def audit_source(source, cell, rate, horizon_s, predict, retain, *, image_size=64):
    """One deployment-style query per origin. Callback receives no scorer data."""
    require(len(source.rgb) > 0 and source.ticks.shape == (len(source.rgb),)
            and np.all(np.diff(source.ticks) > 0) and len(source.phases) == len(source.rgb), 'Strictly ordered complete TRAIN origins required')
    labels = target_labels(source, cell, rate)
    metrics = {key: Metrics(cell, rate) for key in ('MODEL_PRIOR_ZERO', 'ZERO_NORMALIZED', 'HOLD_ORIGIN_NATIVE9')}
    first = int(source.ticks[0])
    for i in range(len(source.rgb)):
        actor = actor_packet(source.rgb[i], source.prior9[i], int(source.ticks[i]), first,
                             cell=cell, horizon_s=horizon_s, image_size=image_size)
        prediction = np.asarray(predict(actor))
        require(prediction.dtype == np.float32 and prediction.shape == (16, 9) and np.isfinite(prediction).all(), 'Actual finite float32 full16 output required')
        # Retain actual prediction before any scoring that could fail.
        retain(i, actor, prediction.copy())
        target = scorer_chunk(source, labels, i)
        metrics['MODEL_PRIOR_ZERO'].add(prediction, target)
        for key, value in baselines(source, i, cell).items():
            metrics[key].add(value, target)
    return dict(queries=len(source.rgb),query_batch_size=1,model_inputs=['actual_saved_TRAIN_RGB', 'causal_state10'],
                future_labels_or_phase_in_actor=False,metrics={key:value.result() for key,value in metrics.items()},
                baseline_actions_executed=False,optimizer_updates=0,physical_io=False,closed_loop_success_evaluated=False)
