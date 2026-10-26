"""ENGINEERING_GLUE: source-bound operator review and adaptive recording advice.

Coverage counts independent original episodes, never resampled segments/frames.
Labels remain operator declarations; this planner cannot certify contacts,
change dataset admission, actuate a robot, or establish learned task mastery.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from explorer_shared.contracts import canonical, digest, event

PROJECTS = ('02', '03', '04', 'personal')
APPROACHES = ('front', 'left', 'right', 'above', 'other')
PHASES = {
    'approach': 'Подведите открытый захват к предмету',
    'grasp': 'Захватите предмет с наблюдаемым удержанием',
    'lift': 'Поднимите предмет над опорой',
    'carry': 'Перенесите предмет к цели',
    'release': 'Отпустите предмет в целевой области',
    'settle': 'Уберите руку и проверьте устойчивый итог',
    'reach': 'Достигните отмеченной цели',
    'contact': 'Установите контакт с предметом',
    'push': 'Сдвиньте предмет в целевую область',
    'verify': 'Проверьте полный итог по записи',
    'hold': 'Сохраните текущее положение без предмета',
}
PLACE = ('approach', 'grasp', 'lift', 'carry', 'release', 'settle')
TASKS = {
    'technical_hold': dict(title='Техническая проверка записи', phases=('hold', 'verify'),
        setup='Оставьте руку в текущей позе. Проверьте видимость руки и синхронную запись.', object_task=False),
    'reach': dict(title='Достичь цели', phases=('approach', 'reach', 'verify'),
        setup='Поставьте метку в уже проверенной рабочей области. Покажите подход с разных сторон.', object_task=False),
    'controlled_push': dict(title='Сдвинуть предмет', phases=('approach', 'contact', 'push', 'verify'),
        setup='Положите лёгкий жёсткий предмет на стол и отметьте целевую область.', object_task=True),
    'pick_and_place': dict(title='Захватить и переложить', phases=PLACE,
        setup='Положите предмет и ёмкость в проверенной рабочей области. В кадре должны быть захват и обе области.', object_task=True),
    'candy_to_tray': dict(title='Конфета → лоток', phases=PLACE,
        setup='Положите конфету в обёртке и лоток на стол. Меняйте начальное положение и направление подхода.', object_task=True),
    'sock_to_basket': dict(title='Носок → корзина', phases=PLACE,
        setup='Положите носок и корзину в проверенной области. В разных показах меняйте складки и положение носка.', object_task=True),
}


def _scope(project_id, task_id=None):
    if project_id not in PROJECTS or (task_id is not None and task_id not in TASKS):
        raise ValueError('Unknown demonstration project or task')


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _local(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError('No retained source artifact')
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Source artifact is unavailable in managed storage')
    return path


def _source(store, job):
    result, root = job.get('result') or {}, Path(store.root).resolve()
    episode_id = result.get('episode_id') or job['config'].get('episode_id')
    if not isinstance(episode_id, str) or not episode_id or episode_id != job['config'].get('episode_id'):
        raise ValueError('Imported original episode identity differs')
    path = _local(root, result.get('dataset_manifest') or result.get('observation_manifest'))
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('Source descriptor exceeds review bound')
    manifest = json.loads(path.read_text())
    descriptors = manifest.get('episodes', [])
    for item in descriptors:
        artifact = _local(root, str(path.parent / item['file']))
        if not artifact.is_relative_to(path.parent) or _sha(artifact) != item['sha256']:
            raise ValueError('Converted episode checksum mismatch')
        if (item.get('source_episode_id') or item['id']) != episode_id:
            raise ValueError('Converted segments are not the selected original episode')
    for name, expected in manifest.get('source_files_sha256', {}).items():
        artifact = _local(root, str(path.parent / name))
        if not artifact.is_relative_to(path.parent) or _sha(artifact) != expected:
            raise ValueError('Retained original checksum mismatch')
    original = manifest
    if manifest.get('source_manifest_path'):
        native = _local(root, manifest['source_manifest_path'])
        if _sha(native) != manifest.get('source_manifest_sha256'):
            raise ValueError('Original recording manifest checksum mismatch')
        original = json.loads(native.read_text())
    native_id = original.get('episode_id', original.get('id'))
    if native_id != episode_id:
        raise ValueError('Retained original identity differs from the import')
    outcomes = {item.get('outcome', 'unknown') for item in descriptors}
    outcome = original.get('outcome') or (next(iter(outcomes)) if len(outcomes) == 1 else 'unknown')
    outcome = outcome if outcome in ('success', 'failure', 'unknown') else 'unknown'
    admission = result.get('training_admission') or manifest.get('training_admission') or {}
    label_basis = manifest.get('action_basis')
    labels_available = bool(descriptors) and label_basis in ('accepted_action', 'dispatched_command', 'host_reported_target_snapshot')
    quality = admission.get('source_quality_usable', labels_available)
    usable = labels_available and quality is True and admission.get('operator_success_label', outcome == 'success') is True and original.get('state') == 'complete'
    key = digest(dict(project_id=job['project_id'], episode_id=episode_id, kind='demonstration'))
    body = dict(source_key=key, source_kind='demonstration', episode_id=episode_id,
        source_manifest_sha256=_sha(path), source_archive_sha256=result.get('source_archive_sha256'),
        source_outcome=outcome, source_training_usable=bool(usable), action_basis=label_basis,
        split=original.get('split', descriptors[0].get('split', 'train') if descriptors else 'unknown'),
        frames=sum(item.get('frames', 0) for item in descriptors) if descriptors else result.get('frames', 0),
        original_session_hint=original.get('clock_domain') or manifest.get('clock_domain'),
        recording_complete=original.get('state') == 'complete',
        integrity_verified=True, annotation_basis='operator_review_not_sensor_ground_truth')
    body['source_fingerprint'] = digest(body)
    return dict(body, import_job_id=job['id'], created=job['created'])


def _evaluation_source(store, job):
    result, root = job.get('result') or {}, Path(store.root).resolve()
    if result.get('mode') != 'physical_attempt' or result.get('physical_io') is not True or result.get('observations', 0) < 1:
        raise ValueError('No completed physical policy attempt observations')
    log = _local(root, result.get('observation_log'))
    rows = json.loads(log.read_text())
    if not isinstance(rows, list) or len(rows) != result['observations']:
        raise ValueError('Policy attempt observation count differs from retained evidence')
    for row in rows:
        frame = _local(root, str(log.parent / row['frame_file']))
        if not frame.is_relative_to(log.parent) or _sha(frame) != row['frame_sha256']:
            raise ValueError('Policy attempt frame checksum mismatch')
    receipts_path = _local(root, str(log.parent / 'dispatch-receipts.json'))
    receipts = json.loads(receipts_path.read_text())
    if not isinstance(receipts, dict) or not any(isinstance(row, dict) and isinstance(row.get('dispatched'), list) and len(row['dispatched']) == 6 for row in receipts.values()):
        raise ValueError('No actual dispatched policy command receipt is retained')
    source = dict(source_key=digest(dict(project_id=job['project_id'], run_id=job['id'], kind='evaluation')),
        source_kind='evaluation', episode_id=job['id'], source_manifest_sha256=_sha(log), dispatch_receipts_sha256=_sha(receipts_path),
        source_outcome='unknown', source_training_usable=False, split='independent_operator_review',
        frames=result['observations'], integrity_verified=True,
        annotation_basis='operator_review_of_policy_attempt_not_automatic_task_score',
        skill_id=result.get('skill_id'))
    source['source_fingerprint'] = digest(source)
    return dict(source, import_job_id=job['id'], created=job['created'])


def _retarget_source(store,job):
    """Raw direct teaching can be reviewed, while missing target labels stay blocked."""
    result,root=job.get('result') or {},Path(store.root).resolve()
    manifest_path=_local(root,result.get('observation_manifest'))
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('format')!='explorer_direct_retarget_observations_v1' or manifest.get('recording_id')!=job['config'].get('episode_id'):
        raise ValueError('Original retarget recording identity differs')
    for name,expected in result['source_files_sha256'].items():
        source=_local(root,str(manifest_path.parent/name))
        if not source.is_relative_to(manifest_path.parent) or _sha(source)!=expected:
            raise ValueError('Original retarget recording checksum mismatch')
    source=dict(source_key=digest(dict(project_id=job['project_id'],episode_id=manifest['recording_id'],kind='demonstration')),
        source_kind='demonstration',episode_id=manifest['recording_id'],source_manifest_sha256=_sha(manifest_path),
        source_archive_sha256=result['source_archive_sha256'],source_outcome=manifest.get('outcome','unknown'),
        source_training_usable=False,action_basis=manifest['action_basis'],split='analysis_only',
        frames=manifest.get('unique_source_frames',0)+manifest.get('unique_target_frames',0),
        recording_complete=manifest.get('state')=='completed',integrity_verified=True,
        annotation_basis='operator_review_not_sensor_ground_truth',block_reasons=result.get('block_reasons',[]))
    source['source_fingerprint']=digest(source)
    return dict(source,import_job_id=job['id'],created=job['created'])


def sources(store, project_id):
    """Catalogue retained completed imports, deduplicated by original recording."""
    _scope(project_id)
    found, invalid, conflicts = {}, [], set()
    for job in sorted(store.jobs(10000), key=lambda row: (row['created'], row['id'])):
        if job['project_id'] == project_id and job['status'] == 'completed' and job['kind'] in ('station_import', 'mobile_import', 'retarget_import', 'station_rollout'):
            try:
                source = _evaluation_source(store, job) if job['kind'] == 'station_rollout' else _retarget_source(store,job) if job['kind']=='retarget_import' else _source(store, job)
                key = source['source_key']
                if key in found and found[key]['source_fingerprint'] != source['source_fingerprint']:
                    conflicts.add(key)
                    raise ValueError('Conflicting imports of the same original episode')
                found.setdefault(key, source)
            except (OSError, ValueError, KeyError, TypeError) as error:
                invalid.append(dict(job_id=job['id'], reason=str(error)))
    return dict(sources=[item for key, item in found.items() if key not in conflicts], invalid_sources=invalid)


def _annotations(db, project_id, task_id):
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.payload.type')='demonstration.review' AND json_extract(body,'$.project_id')=? AND json_extract(body,'$.payload.task_id')=? ORDER BY received,event_id", (project_id, task_id))
    latest = {}
    for row in rows:
        body = json.loads(row['body'])
        payload = body['payload']
        if digest(payload) != body['sha256']:
            raise ValueError('Review event integrity failed')
        latest[payload['source_key']] = dict(payload, revision_sha256=body['sha256'])
    return latest


def _text(body, name, required=True):
    value = body.get(name, '')
    if not isinstance(value, str) or len(value.strip()) > 100 or (required and not value.strip()):
        raise ValueError('Explicit short review field required: ' + name)
    return value.strip()


def annotate(store, project_id, task_id, body):
    """Append an immutable operator review; corrections preserve earlier labels."""
    _scope(project_id, task_id)
    if not isinstance(body, dict):
        raise ValueError('Review must be an object')
    catalogue = sources(store, project_id)
    selected = next((row for row in catalogue['sources'] if row['source_key'] == body.get('source_key')), None)
    if selected is None or selected['source_fingerprint'] != body.get('source_fingerprint'):
        raise ValueError('Select a current verified recording; source evidence changed or is missing')
    phases = body.get('phases', [])
    if not isinstance(phases, list) or any(not isinstance(item, str) for item in phases) or len(set(phases)) != len(phases) or set(phases) - set(TASKS[task_id]['phases']):
        raise ValueError('Review phases must be distinct phases from this task')
    outcome = body.get('outcome')
    if outcome not in ('success', 'failure', 'unknown') or body.get('observed_recording') is not True:
        raise ValueError('An explicit reviewed outcome is required')
    if outcome == 'success' and set(phases) != set(TASKS[task_id]['phases']):
        raise ValueError('A successful full task requires every phase; first grasp is insufficient')
    approach = body.get('approach', 'other')
    if approach not in APPROACHES:
        raise ValueError('Unknown approach direction')
    label = dict(type='demonstration.review', task_id=task_id, source_key=selected['source_key'],
        source_fingerprint=selected['source_fingerprint'], source_kind=selected['source_kind'],
        episode_id=selected['episode_id'], outcome=outcome, phases=[p for p in TASKS[task_id]['phases'] if p in phases],
        scene_id=_text(body, 'scene_id'), session_id=_text(body, 'session_id'),
        object_id=_text(body, 'object_id', TASKS[task_id]['object_task']), approach=approach,
        reviewed_by=_text(body, 'reviewed_by'), observed_recording=True,
        evidence_basis='operator_review', physical_validation_inferred=False)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = _annotations(db, project_id, task_id).get(selected['source_key'])
        old_label = {k: v for k, v in (previous or {}).items() if k not in ('revision_sha256', 'supersedes_sha256')}
        if old_label == label:
            return previous
        expected = body.get('expected_revision_sha256')
        if expected != (previous or {}).get('revision_sha256'):
            raise ValueError('Review changed; refresh before saving a correction')
        label['supersedes_sha256'] = (previous or {}).get('revision_sha256')
        envelope = event('nx', project_id, label, run_id=selected['episode_id'])
        envelope['event_id'] = 'demonstration.review:' + digest(dict(project_id=project_id, payload=label))
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (envelope['event_id'], 'nx', time.time(), envelope['sha256'], canonical(envelope).decode()))
        return dict(label, revision_sha256=envelope['sha256'])


def plan(store, project_id, task_id='pick_and_place'):
    """Outcome-driven recording advice, never a mastery or deployment gate."""
    _scope(project_id, task_id)
    catalogue = sources(store, project_id)
    with store.connect() as db:
        labels = _annotations(db, project_id, task_id)
    current, stale = [], []
    for source in catalogue['sources']:
        review = labels.get(source['source_key'])
        if review and review['source_fingerprint'] == source['source_fingerprint']:
            current.append(dict(source=source, review=review))
        elif review:
            stale.append(source['source_key'])
    demonstrations = [row for row in current if row['source']['source_kind'] == 'demonstration']
    evaluations = [row for row in current if row['source']['source_kind'] == 'evaluation']
    approved = [row for row in demonstrations if row['review']['outcome'] == 'success' and row['source']['source_outcome'] == 'success'
        and row['source']['source_training_usable'] and row['source']['split'] not in ('test', 'sealed_test')]
    phase_counts = Counter(phase for row in approved for phase in row['review']['phases'])
    approach_counts = Counter(row['review']['approach'] for row in approved)
    scenes = {row['review']['scene_id'] for row in approved}
    sessions = {row['review']['session_id'] for row in approved}
    objects = {row['review']['object_id'] for row in approved if row['review']['object_id']}
    missing = [dict(dimension='approach', value=value) for value in ('front', 'left', 'right') if not approach_counts[value]]
    missing += [dict(dimension=name, value='second_distinct_' + name) for name, values in (('scene', scenes), ('session', sessions)) if len(values) < 2]
    if TASKS[task_id]['object_task'] and len(objects) < 2:
        missing.append(dict(dimension='object', value='second_object_or_sock_configuration'))
    judged = [row for row in evaluations if row['review']['outcome'] != 'unknown']
    ordered = sorted(judged, key=lambda row: row['source']['created'])
    halves = (ordered[:len(ordered)//2], ordered[len(ordered)//2:])
    rate = lambda rows: sum(row['review']['outcome'] == 'success' for row in rows) / len(rows) if rows else None
    trend = dict(basis='operator_reviewed_independent_policy_attempts', attempts=len(judged), successes=sum(row['review']['outcome'] == 'success' for row in judged),
        previous_rate=rate(halves[0]) if len(judged) >= 4 else None, recent_rate=rate(halves[1]) if len(judged) >= 4 else None,
        independent_scene_split_verified=False, learned_mastery_claimed=False)
    failures = sum(row['review']['outcome'] == 'failure' for row in demonstrations)
    unreviewed_demos = sum(source['source_kind'] == 'demonstration' and source['source_key'] not in {row['source']['source_key'] for row in current} for source in catalogue['sources'])
    if unreviewed_demos:
        recommendation = dict(stage='review_existing', next_batch=0, reason='Сначала разберите уже перенесённые записи. Отметьте видимые фазы и полный итог; непригодные учебные метки останутся только материалом для анализа.')
    elif len(demonstrations) < 3:
        recommendation = dict(stage='technical_smoke', next_batch=max(1, 3-len(demonstrations)), reason='Сначала проверьте 3 короткие полные записи; при сбоях повторите до 5 технических попыток. Это проверка тракта, а не освоение задачи.')
    elif len(approved) < 3 or failures > len(approved):
        recommendation = dict(stage='repair_recording', next_batch=2, reason='Разберите неудачи и недостающие фазы. Повторите два полных показа в наблюдаемой сцене, затем проверьте качество записи.')
    elif missing:
        recommendation = dict(stage='expand_coverage', next_batch=min(6, max(2, len(missing))), reason='Добавьте показы из непокрытых условий; не повторяйте только одну удачную траекторию.')
    elif len(judged) < 4:
        recommendation = dict(stage='independent_evaluation', next_batch=0, next_evaluation_batch=4-len(judged), reason='Охват пилота есть. Отложите новые показы и независимо проверьте модель в новых попытках. Итог отмечайте только после полной задачи.')
    elif trend['recent_rate'] < trend['previous_rate']:
        recommendation = dict(stage='targeted_corrections', next_batch=3, reason='В последних независимых попытках успех снизился. Сохраните неудачи и соберите три исправляющих показа по их причинам, затем повторите прежние условия.')
    elif trend['recent_rate'] < .8:
        recommendation = dict(stage='targeted_corrections', next_batch=2, reason='Полная задача пока получается нестабильно. Добавьте два показа проблемных условий и повторите независимую проверку.')
    else:
        recommendation = dict(stage='broader_evaluation', next_batch=0, next_evaluation_batch=4, reason='Пилотная тенденция положительная. Проверьте другие сцены, сессии и прежние навыки; эти числа не подтверждают освоение или физическую готовность.')
    return dict(schema='explorer_demonstration_wizard_v1', project_id=project_id, task_id=task_id,
        task=dict(TASKS[task_id], phases=[dict(id=p, instruction=PHASES[p]) for p in TASKS[task_id]['phases']]),
        tasks=[dict(id=key, title=value['title']) for key, value in TASKS.items()], sources=catalogue['sources'],
        reviews=[row['review'] for row in current], invalid_sources=catalogue['invalid_sources'], stale_reviews=stale,
        counts=dict(original_episodes=len({row['source']['episode_id'] for row in demonstrations}), approved_demonstrations=len(approved),
            reviewed_failures=failures, reviewed_unknown=sum(row['review']['outcome'] == 'unknown' for row in demonstrations), unreviewed_sources=len(catalogue['sources'])-len(current)),
        coverage=dict(basis='operator_declared_conditions_not_measured_geometry', phases=dict(phase_counts), approaches=dict(approach_counts), scenes=len(scenes), sessions=len(sessions), objects=len(objects), missing=missing),
        evaluation_trend=trend, recommendation=recommendation, mastery_claimed=False,
        training_admission_changed=False, physical_execution=False,
        controls=dict(keyboard='Штатная панель: I/K, J/L, U/O — XYZ руки; N/M — захват; Shift — точно; Space/Esc — STOP. Для режима суставов смотрите текущую штатную раскладку.',
            leader='Покажите движение SO-101 leader; recenter задаёт начало относительного движения. ROSMASTER требует отдельно измеренного FK/IK-профиля.',
            chassis='Движение шасси сейчас запрещено владельцем. Мобильные показы отложены.'))
