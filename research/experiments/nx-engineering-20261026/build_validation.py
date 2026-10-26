"""ENGINEERING_GLUE: reproduce scoped engineering evidence, never task qualification."""
from pathlib import Path
import hashlib
import json

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
NAMES = {
    'sim': 'sim_pilot-7e21260f8ffb4bfa93b6d3b384123318.json',
    'recorded': 'train_act-447f40e9e1fd45c1ba4f8cda473abaf5.json',
    'hold': 'so101-hold-20261026.json',
    'gpu_initial': 'act-cu132-20261026.json',
    'gpu_retake': 'act-cu132-fp32-20261026.json',
    'recorded_gpu': 'station-gpu-training-job.json',
    'shadow_cpu_checkpoint': 'station-shadow-job.json',
    'shadow_gpu_checkpoint': 'station-shadow-gpu-model.json',
}


def summarize():
    manifest = json.loads((BASE / 'SOURCE_MANIFEST.json').read_text())
    expected = {Path(r['path']).name: r['sha256'] for r in manifest['records']}
    source = {}
    for key, name in NAMES.items():
        data = (BASE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('Preserved evidence bytes changed: ' + name)
        source[key] = json.loads(data)
    sim, rec, hold = source['sim']['result'], source['recorded']['result'], source['hold']
    if rec['source_dataset_id'] != 'station-' + hold['recording']['episode_id']:
        raise ValueError('Recorded training does not reference this stationary episode')
    evaluations = [sim[k].get('evaluation', sim[k]) for k in ['no_motion_baseline', 'teacher', 'student']]
    seeds = [[r['seed'] for r in e['episodes']] for e in evaluations]
    if not seeds[0] == seeds[1] == seeds[2]:
        raise ValueError('Development seeds differ between compared policies')
    latest = hold['samples'][-1]
    gpu_fit = source['recorded_gpu']['result']
    if gpu_fit['source_manifest_sha256'] != rec['source_manifest_sha256']:
        raise ValueError('Recorded CPU/GPU fits have different source manifests')
    for key, job_id in [('shadow_cpu_checkpoint', source['recorded']['id']),
                        ('shadow_gpu_checkpoint', source['recorded_gpu']['id'])]:
        if Path(source[key]['result']['checkpoint']).parent.name != job_id:
            raise ValueError('Shadow checkpoint does not reference its labelled training run')
    summary = {
        'engineering_only': True,
        'hypothesis_status': {f'H{i}': 'UNTESTED' for i in range(1, 6)},
        'sources': manifest['records'],
        'simulation': {
            'run_id': source['sim']['id'], 'evaluation_role': 'DEVELOPMENT_NOT_SEALED_FINAL',
            'physical_io': sim['physical_io'], 'teacher_environment_steps': sim['teacher']['environment_timesteps'],
            'teacher_optimizer_updates': sim['teacher']['optimizer_updates'],
            'act_optimizer_steps': sim['student']['optimizer_steps'], 'reported_elapsed_s': sim['elapsed_s'],
            'framework_versions': sim['student']['framework_versions'], 'matched_evaluation_seeds': seeds[0],
            'outcomes': {label: {'successes': e['successes'], 'attempts': e['n']} for label, e in zip(
                ['no_motion', 'ppo_privileged_state', 'act_rgb_command_estimate'], evaluations)},
            'official_dataset_export': sim['dataset']['official_export'],
            'historical_data_selection_caveat': 'This historical run used all12 teacher episodes as BC targets, including2failed. It does not validate the subsequent success-only filter. Integrator correction context; raw source is preserved.',
        },
        'stationary_recording': {
            'episode_id': hold['recording']['episode_id'], 'recorded_frames': hold['recording']['samples'],
            'status_snapshots': len(hold['samples']),
            'before_to_last_observation_window_s': (latest['measurements']['monotonic_ns'] - hold['before']['measurements']['monotonic_ns']) / 1e9,
            'first_to_last_status_window_s': (latest['measurements']['monotonic_ns'] - hold['samples'][0]['measurements']['monotonic_ns']) / 1e9,
            'reported_max_normalized_position_deviation': hold['max_normalized_position_deviation'],
            'configuration_writes': hold['configuration_writes'],
            'last_snapshot_loop_hz': latest['loop_hz'], 'last_snapshot_period_p95_ms': latest['period_p95_ms'],
            'source_recording_outcome_label': hold['recording']['outcome'],
            'label_scope': 'Technical current-pose hold/record check; no object-task completion claim',
            'source_actor_context': 'automated_engineering_test, communicated by integrator; not a human task demonstration',
            'resolution_caveat': 'Reported normalized motor coordinates; zero does not bound world-pose error, force or between-snapshot motion',
        },
        'recorded_training': {
            'run_id': source['recorded']['id'], 'source_dataset_id': rec['source_dataset_id'],
            'source_manifest_sha256': rec['source_manifest_sha256'], 'source_frames': hold['recording']['samples'],
            'action_dimensions': rec['feature_dimension'], 'state_basis': rec['state_basis'],
            'optimizer_steps': rec['optimizer_steps'], 'weights_changed': rec['weights_changed'],
            'save_reload_passed': rec['save_reload_passed'], 'reload_max_abs_error': rec['reload_max_abs_error'],
            'reported_elapsed_s': rec['elapsed_s'], 'physical_io_during_training': rec['physical_io'],
            'simulator_executed': rec['simulator_executed'], 'independent_evaluation_available': rec['evaluation']['available'],
            'evaluation': rec['evaluation'], 'framework_versions': rec['framework_versions'],
            'scope': 'Fit of a recorded stationary six-axis pose; no independent task-success measurement',
        },
        'gpu': {
            'scope': 'Compact six-axis ACT, synthetic tensors, two optimizer updates per attempt',
            'strict_parity_criterion': {'value': 1e-4, 'source': 'Integrator-stated engineering criterion; not embedded in raw snapshots'},
            'attempts': [{
                'id': key, 'passed': source[key]['passed'], 'optimizer_steps': source[key]['optimizer_steps'],
                'finite': source[key]['finite'], 'weights_changed': source[key]['weights_changed'],
                'save_reload_max_abs_error': source[key]['save_reload_max_abs_error'],
                'cpu_gpu_max_abs_error': source[key]['cpu_gpu_max_abs_error'],
                'reported_elapsed_s': source[key]['elapsed_s'], 'tf32_enabled': source[key].get('tf32_enabled', 'NOT_RECORDED'),
                'framework_versions': source[key]['framework_versions'],
                'framework_compatibility_note': source[key]['framework_compatibility_note'],
                'compute_capability': source[key]['compute_capability'], 'compiled_architectures': source[key]['compiled_architectures'],
            } for key in ['gpu_initial', 'gpu_retake']],
            'limits': 'Runtime-specific dependency exception; compiled architecture metadata omits sm_87. Two successful updates do not qualify every operation, model size or deployed station inference. Retake is not a controlled causal TF32 experiment.',
        },
        'recorded_gpu_training': {
            'run_id': source['recorded_gpu']['id'], 'source_manifest_sha256': gpu_fit['source_manifest_sha256'],
            'requested_device': source['recorded_gpu']['config']['device'],
            'execution_context': 'Integrator reports actual GPU execution; not a matched CPU/GPU speed benchmark',
            'optimizer_steps': gpu_fit['optimizer_steps'], 'weights_changed': gpu_fit['weights_changed'],
            'reload_max_abs_error': gpu_fit['reload_max_abs_error'], 'reported_elapsed_s': gpu_fit['elapsed_s'],
            'framework_versions': gpu_fit['framework_versions'],
            'framework_compatibility_note': gpu_fit['framework_compatibility_note'],
            'evaluation': gpu_fit['evaluation'], 'checkpoint_sha256': gpu_fit['checkpoint_sha256'],
            'physical_io_during_training': gpu_fit['physical_io'], 'simulator_executed': gpu_fit['simulator_executed'],
        },
        'live_shadow': [{
            'run_id': source[key]['id'], 'checkpoint_origin': origin,
            'checkpoint_path': source[key]['result']['checkpoint'],
            'observations': source[key]['result']['observations'],
            'reported_elapsed_s': source[key]['result']['elapsed_s'],
            'max_predicted_displacement_normalized_motor_units': source[key]['result']['max_predicted_displacement_from_measured'],
            'outcome': source[key]['result']['outcome'],
            'physical_actuation': source[key]['result']['physical_io'],
            'physical_success_inferred': source[key]['result']['physical_success_inferred'],
            'runtime_framework_versions': source[key]['result']['framework_versions'],
            'inference_device_scope': 'Existing runtime uses CPU; GPU-trained checkpoint does not imply GPU inference',
            'observation_log_reported_path': source[key]['result']['observation_log'],
            'log_image_hash_audit_scope': 'Raw remote observation logs/images not independently checked in this research update',
        } for key, origin in [('shadow_cpu_checkpoint', '120-update CPU fit'),
                             ('shadow_gpu_checkpoint', '480-update GPU fit')]],
        'not_measured': ['independent physical task success', 'sealed final evaluation', 'matched CPU/GPU speedup', 'physical transfer or handover success', 'H1-H5 effects'],
    }
    (BASE / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def plot(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'text.usetex': True, 'font.family': 'serif', 'font.serif': ['Times'],
                         'font.size': 8, 'axes.titlesize': 8, 'axes.labelsize': 8,
                         'xtick.labelsize': 7, 'ytick.labelsize': 7})
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.35), gridspec_kw={'wspace': .42})
    outcomes = list(summary['simulation']['outcomes'].values())
    values = [o['successes'] for o in outcomes]
    axes[0].bar(range(3), values, color=['#71717a', '#2563eb', '#d97706'], width=.6)
    axes[0].set_xticks(range(3), ['No motion', 'PPO\n(privileged)', 'ACT\n(RGB + command)'])
    axes[0].set_yticks([0, 2, 4, 6]); axes[0].set_ylim(0, 7.1)
    axes[0].set_ylabel('Confirmed simulated reach attempts')
    axes[0].set_title('(a) Historical development reach check')
    for i, outcome in enumerate(outcomes):
        axes[0].text(i, outcome['successes'] + .18, f"{outcome['successes']}/{outcome['attempts']}", ha='center')
    errors = [a['cpu_gpu_max_abs_error'] for a in summary['gpu']['attempts']]
    axes[1].scatter([0], [errors[0]], color='#b45309', marker='x', s=52, linewidths=1.5, zorder=3)
    axes[1].scatter([1], [errors[1]], color='#2563eb', marker='o', s=40, zorder=3)
    axes[1].set_xlim(-.4, 1.4)
    axes[1].set_yscale('log'); axes[1].set_ylim(3e-7, 5e-4)
    axes[1].axhline(1e-4, color='#52525b', linewidth=1, linestyle='--')
    axes[1].text(.98, 1.35e-4, r'$10^{-4}$ criterion', ha='right', fontsize=7,
                 transform=axes[1].get_yaxis_transform())
    axes[1].set_xticks(range(2), ['Initial check\n(failed parity)', 'Retake\n(TF32 disabled)'])
    axes[1].set_ylabel('Maximum CPU/GPU absolute difference')
    axes[1].set_title('(b) Synthetic ACT numerical check')
    for i, value in enumerate(errors):
        axes[1].text(i, value * 1.25, f'{value:.7g}', ha='center', fontsize=7)
    for ax in axes:
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    fig.subplots_adjust(left=.08, right=.98, bottom=.25, top=.87)
    output = ROOT / 'research/paper/figures'; output.mkdir(exist_ok=True)
    fig.savefig(output / 'engineering_checks.pdf', metadata={'Author': '', 'Title': 'Engineering development checks',
                                                            'CreationDate': None, 'ModDate': None})
    fig.savefig(output / 'engineering_checks.png', dpi=200)
    plt.close(fig)
    summary['plot_runtime'] = {'matplotlib': matplotlib.__version__}
    (BASE / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    plot(summarize())
