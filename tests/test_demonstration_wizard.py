"""Meaningful source, annotation and adaptive advice invariants; never devices."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from explorer.store import Store
from explorer.demonstration_wizard import TASKS, annotate, plan, sources


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_episode(store, episode_id, *, project='02', outcome='success', split='train', segments=1, usable=True, complete=True):
    folder = store.root/'datasets'/episode_id
    folder.mkdir(parents=True, exist_ok=True)
    native = folder/'original.json'
    native.write_text(json.dumps(dict(id=episode_id, outcome=outcome, state='complete' if complete else 'interrupted', split=split, clock_domain='fixture:session')))
    episodes = []
    for index in range(segments):
        path = folder/(str(index)+'.npz')
        path.write_bytes(('retained-fixture-payload-'+str(index)).encode())
        episodes.append(dict(id=episode_id if segments == 1 else episode_id+'-segment-'+str(index), source_episode_id=episode_id,
            file=path.name, sha256=sha(path), frames=10, outcome=outcome, split=split))
    descriptor = folder/'manifest.json'
    descriptor.write_text(json.dumps(dict(format='explorer_robot_episode_v1', source_manifest_path=str(native), source_manifest_sha256=sha(native),
        source_files_sha256={'original.json':sha(native)}, action_basis='dispatched_command', episodes=episodes)))
    job = store.create_job('station_import', project, dict(episode_id=episode_id))
    store.complete_job(job['id'], dict(episode_id=episode_id, dataset_manifest=str(descriptor), source_archive_sha256='fixture-only',
        training_admission=dict(source_quality_usable=usable, operator_success_label=outcome == 'success')))
    return job, descriptor


def review(store, episode_id, *, task='pick_and_place', project='02', outcome='success', scene='scene-a', session='session-a', approach='front', object_id='object-a'):
    source = next(row for row in sources(store, project)['sources'] if row['episode_id'] == episode_id)
    body = dict(source_key=source['source_key'], source_fingerprint=source['source_fingerprint'], outcome=outcome,
        phases=list(TASKS[task]['phases']), scene_id=scene, session_id=session, object_id=object_id,
        approach=approach, reviewed_by='software-test-operator-fixture', observed_recording=True)
    return annotate(store, project, task, body), body


def test_empty_plan_starts_with_three_technical_recordings_and_no_mastery(tmp_path):
    result = plan(Store(tmp_path), '02')
    assert result['recommendation']['stage'] == 'technical_smoke'
    assert result['recommendation']['next_batch'] == 3
    assert result['counts']['approved_demonstrations'] == 0
    assert not result['mastery_claimed'] and not result['training_admission_changed']
    assert result['physical_execution'] is False


def test_only_verified_originals_count_and_resampled_segments_count_once(tmp_path):
    store = Store(tmp_path)
    job, path = import_episode(store, 'original', segments=3)
    review(store, 'original')
    duplicate = store.create_job('station_import', '02', dict(episode_id='original'))
    store.complete_job(duplicate['id'], store.job(job['id'])['result'])
    result = plan(store, '02')
    assert len(result['sources']) == 1
    assert result['sources'][0]['frames'] == 30
    assert result['counts']['original_episodes'] == 1
    assert result['counts']['approved_demonstrations'] == 1
    assert all(value == 1 for value in result['coverage']['phases'].values())
    (path.parent/'0.npz').write_bytes(b'tampered')
    damaged = plan(store, '02')
    assert not damaged['sources'] and damaged['counts']['approved_demonstrations'] == 0
    assert len(damaged['invalid_sources']) == 2


def test_a_first_grasp_or_invented_phase_cannot_be_a_successful_full_task(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'one')
    _, body = review(store, 'one')
    for phases in (['approach', 'grasp'], ['grasp', 'grasp'], ['imagined_contact'], 'grasp'):
        with pytest.raises(ValueError, match='phase'):
            annotate(store, '02', 'pick_and_place', dict(body, phases=phases))


def test_failure_unknown_bad_source_and_sealed_test_never_become_expert_coverage(tmp_path):
    store = Store(tmp_path)
    for name, options in [('failed', dict(outcome='failure')), ('unknown', dict(outcome='unknown')), ('bad-quality', dict(usable=False)), ('sealed', dict(split='sealed_test')), ('interrupted', dict(complete=False))]:
        import_episode(store, name, **options)
        review(store, name, outcome='success')
    result = plan(store, '02')
    assert result['counts']['original_episodes'] == 5
    assert result['counts']['approved_demonstrations'] == 0
    assert result['coverage']['phases'] == {}
    assert result['recommendation']['stage'] == 'repair_recording'
    assert all(row['reviewed_by'] == 'software-test-operator-fixture' for row in result['reviews'])


def test_failed_and_unknown_reviewed_attempts_remain_separate(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'one', outcome='failure')
    review(store, 'one', outcome='failure')
    import_episode(store, 'two', outcome='unknown')
    review(store, 'two', outcome='unknown')
    result = plan(store, '02')
    assert result['counts']['reviewed_failures'] == 1
    assert result['counts']['reviewed_unknown'] == 1
    assert result['counts']['approved_demonstrations'] == 0


def test_review_idempotence_and_optimistic_corrections_preserve_audit_trail(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'one')
    first, body = review(store, 'one')
    assert annotate(store, '02', 'pick_and_place', body) == first
    with pytest.raises(ValueError, match='refresh'):
        annotate(store, '02', 'pick_and_place', dict(body, outcome='failure'))
    second = annotate(store, '02', 'pick_and_place', dict(body, outcome='failure', expected_revision_sha256=first['revision_sha256']))
    assert second['supersedes_sha256'] == first['revision_sha256']
    assert plan(store, '02')['counts']['reviewed_failures'] == 1
    assert plan(store, '02')['counts']['approved_demonstrations'] == 0
    with pytest.raises(ValueError, match='refresh'):
        annotate(store, '02', 'pick_and_place', dict(body, scene_id='concurrent-stale-scene', expected_revision_sha256=first['revision_sha256']))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.payload.type')='demonstration.review'").fetchone()[0] == 2
    restored = Store(tmp_path/'restored')
    store.backup(restored.path)
    assert plan(restored, '02')['counts']['original_episodes'] == 0  # DB copy alone cannot fabricate missing payloads.


def test_project_isolation_and_no_client_selected_files_or_fake_episodes(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'personal-only', project='personal')
    _, body = review(store, 'personal-only', project='personal')
    assert not plan(store, '02')['sources']
    with pytest.raises(ValueError, match='verified recording'):
        annotate(store, '02', 'pick_and_place', body)
    with pytest.raises(ValueError):plan(store, '../outside')
    with pytest.raises(ValueError):plan(store, '02', 'invented-task')


def test_changed_descriptor_invalidates_review_and_old_fingerprint(tmp_path):
    store = Store(tmp_path)
    _, path = import_episode(store, 'one')
    _, body = review(store, 'one')
    descriptor = json.loads(path.read_text());descriptor['new_metadata'] = True
    path.write_text(json.dumps(descriptor))
    assert plan(store, '02')['stale_reviews'] == [body['source_key']]
    assert plan(store, '02')['counts']['approved_demonstrations'] == 0
    with pytest.raises(ValueError, match='changed'):
        annotate(store, '02', 'pick_and_place', body)


def covered(store):
    for index, direction in enumerate(('front', 'left', 'right')):
        episode = 'demo-'+str(index)
        import_episode(store, episode)
        review(store, episode, scene='scene-'+str(index % 2), session='session-'+str(index % 2), object_id='object-'+str(index % 2), approach=direction)


def evaluation(store, outcome, index):
    job = store.create_job('station_rollout', '02', {})
    folder = store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    frame = folder/'frame.jpg';frame.write_bytes(b'fixture-frame')
    log = folder/'policy-observations.json'
    log.write_text(json.dumps([dict(frame_file=frame.name, frame_sha256=sha(frame))]))
    (folder/'dispatch-receipts.json').write_text(json.dumps({'0':dict(dispatched=[0]*6, control_epoch='fixture')}))
    store.complete_job(job['id'], dict(mode='physical_attempt', physical_io=True, observations=1, observation_log=str(log), skill_id='fixture-skill'))
    review(store, job['id'], outcome=outcome, scene='eval-scene-'+str(index), session='eval-session-'+str(index))
    return job


def test_recommendations_depend_on_coverage_then_evaluation_instead_of_fixed_count(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'one');review(store, 'one')
    import_episode(store, 'two');review(store, 'two')
    import_episode(store, 'three');review(store, 'three')
    assert plan(store, '02')['recommendation']['stage'] == 'expand_coverage'
    second = Store(tmp_path/'second')
    covered(second)
    assert plan(second, '02')['recommendation']['stage'] == 'independent_evaluation'
    assert plan(second, '02')['recommendation']['next_batch'] == 0
    for index, outcome in enumerate(('success', 'success', 'failure', 'failure')):
        evaluation(second, outcome, index)
    result = plan(second, '02')
    assert result['recommendation']['stage'] == 'targeted_corrections'
    assert result['recommendation']['next_batch'] == 3
    assert result['evaluation_trend']['previous_rate'] == 1
    assert result['evaluation_trend']['recent_rate'] == 0
    assert result['counts']['approved_demonstrations'] == 3
    assert not result['evaluation_trend']['independent_scene_split_verified']


def test_shadow_or_no_dispatch_attempt_cannot_supply_task_evaluation(tmp_path):
    store = Store(tmp_path)
    job = store.create_job('station_rollout', '02', {})
    folder = store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    log = folder/'policy-observations.json';log.write_text('[]')
    store.complete_job(job['id'], dict(mode='shadow', physical_io=False, observations=0, observation_log=str(log)))
    result = plan(store, '02')
    assert result['evaluation_trend']['attempts'] == 0
    assert not result['sources'] and len(result['invalid_sources']) == 1


def test_success_is_never_prechecked_from_only_recorded_outcome(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'unreviewed-success')
    result = plan(store, '02')
    assert result['counts']['approved_demonstrations'] == 0
    assert result['counts']['unreviewed_sources'] == 1
    source = result['sources'][0]
    with pytest.raises(ValueError, match='reviewed outcome'):
        annotate(store, '02', 'pick_and_place', dict(source_key=source['source_key'], source_fingerprint=source['source_fingerprint'], outcome='success', phases=list(TASKS['pick_and_place']['phases'])))


def test_declared_file_escape_and_original_identity_mismatch_are_excluded(tmp_path):
    store = Store(tmp_path)
    _, path = import_episode(store, 'escape')
    descriptor = json.loads(path.read_text());outside = tmp_path/'outside.bin';outside.write_bytes(b'x')
    descriptor['episodes'][0]['file'] = '../outside.bin'
    descriptor['episodes'][0]['sha256'] = sha(outside)
    path.write_text(json.dumps(descriptor))
    assert not sources(store, '02')['sources']


def test_available_unreviewed_recording_is_reviewed_before_asking_for_more(tmp_path):
    store = Store(tmp_path)
    import_episode(store, 'available')
    result = plan(store, '02')
    assert result['recommendation']['stage'] == 'review_existing'
    assert result['recommendation']['next_batch'] == 0
    review(store, 'available')
    assert plan(store, '02')['recommendation']['stage'] == 'technical_smoke'


def test_ui_minimal_path_preserves_review_and_only_calls_wizard_endpoints(tmp_path):
    import shutil
    import subprocess
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed for the existing native JavaScript UI path')
    store = Store(tmp_path/'state')
    import_episode(store, 'ui-fixture')
    response = plan(store, '02')
    response['task']['setup'] = '<img src=x onerror=bad()>'
    response_file = tmp_path/'plan.json';response_file.write_text(json.dumps(response))
    ui = Path(__file__).resolve().parents[1]/'00-control-center/src/explorer/static/demonstration-wizard.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const data = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const events = {}, calls = [];
const context = vm.createContext({
  document: { addEventListener: (name, handler) => { (events[name] ||= []).push(handler); } },
  model: {}, queueMicrotask, console,
  api: async (path) => { calls.push(['GET', path]); return data; },
  post: async (path, body) => { calls.push(['POST', path, body]); return {...body, revision_sha256: 'fixture-revision'}; },
  render: () => {}, toast: () => {},
  esc: (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
  kv: (key, value) => `<dt>${key}</dt><dd>${value}</dd>`
});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
(async () => {
  await vm.runInContext('refreshDemonstrationWizard("02")', context);
  const state = vm.runInContext('demonstrationWizardState("02")', context);
  state.selected = data.sources[0].source_key;
  state.draft = { scene_id: 'scene', session_id: 'session', object_id: 'candy', approach: 'front', outcome: 'success', reviewed_by: 'operator-fixture', phases: data.task.phases.map(p => p.id), observed_recording: true };
  const html = vm.runInContext('demonstrationWizardPanel("02")', context);
  assert(html.includes('&lt;img src=x onerror=bad()&gt;'));
  assert(!html.includes('<img src=x'));
  assert(html.includes('demonstration-review-form') && html.includes('demo-observed'));
  const panel = {dataset: {demoProject: '02'}};
  let prevented = false;
  await events.submit[0]({target: {id: 'demonstration-review-form', closest: () => panel}, preventDefault: () => {prevented = true;}});
  assert(prevented);
  const sent = calls.find(row => row[0] === 'POST');
  assert.equal(sent[1], '/api/demonstration-wizard/annotation');
  assert.equal(sent[2].source_fingerprint, data.sources[0].source_fingerprint);
  assert.equal(sent[2].outcome, 'success');
  assert.deepEqual(sent[2].phases, data.task.phases.map(p => p.id));
  assert(calls.every(row => row[1].startsWith('/api/demonstration-wizard')));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run([node, '-e', script, str(response_file), str(ui)], check=True, capture_output=True, text=True)
