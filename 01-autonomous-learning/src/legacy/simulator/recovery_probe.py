"""Saved-input counterfactuals with fixed weights/preprocessing; no dynamics."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch

from .learning import load_policy, sha256, write_json
from .room_policy import issued_command
from src.mobile_policy_contract import clock_features


def replay(diagnostic, output):
    diagnostic, output = Path(diagnostic), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    trace = next((diagnostic / 'trial/observed').glob('policy-pick-*/actions.jsonl'))
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    refusal = next(row for row in reversed(rows) if row.get('status') == 'refused')
    exposure = next(row for row in rows if row.get('event') == 'observation' and row['step'] == refusal['step'])
    rgb = np.asarray(Image.open(trace.parent / exposure['image']).convert('RGB'))
    state = np.asarray(exposure['command'], dtype=np.float32)
    torch.set_num_threads(1)
    actor = load_policy(diagnostic / 'model', device='cpu')
    context = actor.bundle['actor_context']
    records = []
    elapsed = refusal['decision']['elapsed_s']
    for clock in [elapsed, 0., 6., 12., 24., 35., 50.]:
        actor.reset()
        inputs = {'observation.state':torch.tensor(state[None]),
            'observation.images.wrist':torch.from_numpy(rgb.copy()).permute(2, 0, 1).float()[None]/255.,
            context['feature']:torch.tensor([clock_features(context, clock)], dtype=torch.float32)}
        processed = actor.pre(inputs)
        with torch.no_grad():
            chunk = actor.policy.predict_action_chunk(processed)
            physical = torch.stack([actor.post(chunk[:, i].clone()) for i in range(chunk.shape[1])], dim=1)
        prediction = physical[0, 0].cpu().numpy()
        try:
            issued = issued_command(state, prediction, np.zeros(6))
            guard = dict(accepted=True, issued=issued.tolist())
        except ValueError as error:
            guard = dict(accepted=False, reason=str(error))
        name = f'inputs-{len(records):02d}.npz'
        np.savez_compressed(output/name, **{
            'raw_' + k.replace('.', '_'):v.detach().cpu().numpy() for k,v in inputs.items()}, **{
            'normalized_' + k.replace('.', '_'):v.detach().cpu().numpy()
            for k,v in processed.items() if isinstance(v, torch.Tensor)})
        records.append(dict(clock_s=clock, raw_inputs_and_processed=name,
            full_normalized_chunk=chunk.cpu().numpy().tolist(), full_physical_chunk=physical.cpu().numpy().tolist(),
            guard=guard))
    error = float(np.max(abs(np.asarray(records[0]['full_physical_chunk'])[0, 0] - refusal['decision']['prediction'])))
    result = dict(kind='saved_input_clock_counterfactual', checkpoint_sha256=actor.bundle['checkpoint_sha256'],
        trace_sha256=sha256(trace), exposure=exposure, recorded_refusal=refusal,
        max_abs_reproduction_error=error, records=records, training_updates=0, physics_steps=0,
        limitations=['Original full physical state at refusal unavailable: exact physical replay not claimed',
            'Changing clock alone tests sensitivity, not causal success or a retrained no-clock policy'],
        physical_ready=False)
    write_json(output/'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = replay(args.diagnostic, args.output)
    print(json.dumps(dict(max_abs_reproduction_error=value['max_abs_reproduction_error'],
        guard=value['records'][0]['guard'], clock_variants=len(value['records']))))
