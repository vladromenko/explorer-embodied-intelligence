"""Pure diagnostic contracts. No model training, simulator stepping, or robot I/O.

Physics/actuator limits remain owned by the existing validator and issuer.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
import math

import numpy as np

SCHEMA = 'recovery_stationary_grasp_v2'


def vector9(value, label='command'):
    result = np.asarray(value, dtype=float)
    if result.shape != (9,) or not np.isfinite(result).all():
        raise ValueError(f'{label}: expected nine finite numbers')
    return result.copy()


def validate_stats(stats):
    if len(stats) != 4:
        raise ValueError('Expected state mean/std and action mean/std')
    values = [vector9(value, 'normalizer') for value in stats]
    if np.any(values[1] <= 0) or np.any(values[3] <= 0):
        raise ValueError('Normalizer standard deviations must be positive')
    return values


@dataclass(frozen=True)
class CommandPlan:
    generation: int
    reference: np.ndarray
    raw: np.ndarray
    issued: np.ndarray
    residual_before: np.ndarray
    residual_after: np.ndarray


class StationaryExecutor:
    """One raw-validation/quantization path for teacher, student, and hold.

    Prepare never commits residuals. Commit only after a complete env.step;
    a refused or partly applied action cannot silently advance host state.
    """
    def __init__(self, validate: Callable, issue: Callable):
        self.validate = validate
        self.issue = issue
        self.residual = np.zeros(6)
        self.generation = 0

    def reset(self):
        self.residual[:] = 0.
        self.generation += 1

    def prepare(self, state, prediction):
        reference = vector9(state, 'reference')
        raw = vector9(prediction, 'raw prediction')
        self.validate(reference)
        self.validate(raw)  # Validate all nine raw channels BEFORE base locking.
        if np.any(np.abs(reference[6:]) > 1e-9):
            raise ValueError('Stationary executor requires an already stopped base')
        candidate = self.residual.copy()
        issued = vector9(self.issue(reference, raw, candidate), 'issued command')
        self.validate(issued)
        if np.max(np.abs(issued[:6] - reference[:6])) > 2.000001:
            raise ValueError('Existing two-degree issued limit was violated')
        if np.any(np.abs(issued[6:]) > 1e-9):
            raise ValueError('Stationary issuer did not lock the base')
        return CommandPlan(self.generation, reference, raw, issued,
                           self.residual.copy(), candidate.copy())

    def commit(self, plan: CommandPlan, applied):
        actual = vector9(applied, 'applied command estimate')
        if plan.generation != self.generation:
            raise ValueError('Stale or already committed command plan')
        if not np.allclose(actual, plan.issued, atol=1e-6, rtol=0):
            raise ValueError('Issued/applied endpoint mismatch; residual not committed')
        self.residual[:] = plan.residual_after
        self.generation += 1


class HoldWindow:
    """Independent post-stop window; old accumulated hold time is not an input."""
    def __init__(self, start_time, minimum_s, max_sample_gap_s, initial_condition):
        if not all(math.isfinite(x) for x in (start_time, minimum_s, max_sample_gap_s)):
            raise ValueError('Non-finite hold-window timing')
        if minimum_s <= 0 or max_sample_gap_s <= 0:
            raise ValueError('Positive hold timing required')
        self.start_time = float(start_time)
        self.last_time = float(start_time)
        self.minimum_s = float(minimum_s)
        self.max_gap_s = float(max_sample_gap_s)
        self.all_valid = bool(initial_condition)
        self.samples = 0

    def observe(self, stamp, condition):
        dt = float(stamp) - self.last_time
        if not math.isfinite(dt) or dt <= 0 or dt > self.max_gap_s + 1e-8:
            raise ValueError('Post-stop observer missed or reordered a physics step')
        self.last_time = float(stamp)
        self.all_valid = self.all_valid and bool(condition)
        self.samples += 1

    def summary(self):
        elapsed = self.last_time - self.start_time
        return dict(success=bool(self.samples > 0 and self.all_valid and
                                 elapsed >= self.minimum_s - 1e-9),
                    elapsed_s=elapsed, minimum_s=self.minimum_s,
                    substep_samples=self.samples, all_samples_valid=self.all_valid,
                    previous_hold_time_ignored=True)


def safe_image_path(episode, name):
    root = Path(episode).resolve()
    relative = Path(name)
    target = (root / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root):
        raise ValueError('Image path escapes its episode')
    if not target.is_file():
        raise ValueError(f'Missing teacher image: {name}')
    return target


def validate_teacher_rows(rows: Sequence[dict], validate: Callable, period=.05, require_expert=True):
    """Validate sequence structure, not just each row in isolation.

    Applied values are command estimates, NOT measured joint positions.
    Calling this does not prove that the teacher grasped an object.
    """
    if not rows:
        raise ValueError('Empty teacher episode')
    previous = None
    for index, row in enumerate(rows):
        if type(row.get('step')) is not int or row['step'] != index:
            raise ValueError('Teacher row indices must be contiguous from zero')
        if type(row.get('training_target_valid')) is not bool or (require_expert and not row['training_target_valid']):
            raise ValueError('Student/refused targets are not teacher labels')
        if row.get('joint_state_source') != 'command_estimate':
            raise ValueError('Unknown observation-state semantics')
        values = {key: vector9(row[key], key) for key in
                  ('observation_state', 'raw_action', 'issued_action', 'applied_action')}
        for value in values.values():
            validate(value)
        before, after = float(row['simulation_time']), float(row['post_simulation_time'])
        if not math.isfinite(before + after) or abs(after - before - period) > 1e-7:
            raise ValueError('Teacher action duration does not equal the declared period')
        state, applied = values['observation_state'], values['applied_action']
        if np.max(np.abs(applied[:6] - state[:6])) > 2.000001:
            raise ValueError('Teacher applied step exceeds two degrees')
        if np.max(np.abs(values['raw_action'][:6] - state[:6])) > 20.000001:
            raise ValueError('Teacher raw step exceeds the unchanged twenty-degree guard')
        if not np.allclose(values['issued_action'], applied, atol=1e-6, rtol=0):
            raise ValueError('Teacher issued/applied endpoint mismatch')
        stopped_keys=(values if row['training_target_valid'] else
            ('observation_state','issued_action','applied_action'))
        if any(np.any(np.abs(values[key][6:]) > 1e-9) for key in stopped_keys):
            raise ValueError('Stationary teacher has nonzero base commands')
        if previous is not None:
            if abs(before - float(previous['post_simulation_time'])) > 1e-7:
                raise ValueError('Gap or overlap between teacher timestamps')
            if not np.allclose(state, previous['applied_action'], atol=1e-6, rtol=0):
                raise ValueError('Teacher state is disconnected from previous applied action')
        previous = row


def encode_anchor_delta(actions, anchor):
    """Offline ablation codec only: each chunk target is relative to SAME anchor.

    Not incremental deltas between adjacent future actions; not enabled in a
    policy/runtime and not a safety filter.
    """
    action = np.asarray(actions, dtype=float)
    base = vector9(anchor, 'delta anchor')
    if action.ndim != 2 or action.shape[1] != 9 or not np.isfinite(action).all():
        raise ValueError('Expected a finite [horizon,9] action chunk')
    result = action.copy()
    result[:, :6] -= base[:6]
    return result


def decode_anchor_delta(deltas, anchor):
    delta = np.asarray(deltas, dtype=float)
    base = vector9(anchor, 'delta anchor')
    if delta.ndim != 2 or delta.shape[1] != 9 or not np.isfinite(delta).all():
        raise ValueError('Expected a finite [horizon,9] delta chunk')
    result = delta.copy()
    result[:, :6] += base[:6]
    return result


def evidence_value(value):
    """JSON-safe evidence; nonfinite values stay marked, never become commands."""
    if isinstance(value, np.ndarray):
        return evidence_value(value.tolist())
    if isinstance(value, np.generic):
        return evidence_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": repr(value)}
    if isinstance(value, dict):
        return {str(key): evidence_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [evidence_value(item) for item in value]
    return value
