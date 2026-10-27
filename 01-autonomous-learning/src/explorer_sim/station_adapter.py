"""Pure, timestamp-checked station recording conversion. ENGINEERING_GLUE.

Reads existing images, measured states and accepted/dispatched actions only;
there are no motor, camera capture, network or calibration write operations.
"""
import fcntl
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from .pilot import sha, utc, write_json


def convert_station_episode(source_manifest_path, target_dir, *, capture_manifest_path=None, expected_capture_sha256=None):
    source_path, target = Path(source_manifest_path).resolve(), Path(target_dir).resolve()
    source_digest = sha(source_path)
    source = json.loads(source_path.read_text())
    episode_id, split = source.get("episode_id"), source.get("split", "train")
    if not isinstance(episode_id, str) or not episode_id or split not in ("train", "imitation_train", "development", "validation", "sealed_test", "test"):
        raise ValueError("Explicit episode identity and valid split are required")
    if source.get("source_type") != "station_demonstration" or source.get("state_basis") not in ("measured_state", "command_estimate"):
        raise ValueError("An explicit station demonstration state basis is required")
    if source.get("action_basis") not in ("accepted_action", "dispatched_command"):
        raise ValueError("Requested/operator-input actions are not accepted as applied demonstration labels")
    samples = source.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Camera-only recordings have no synchronized state/action samples")
    names, units = source.get("action_names"), source.get("action_units")
    if not isinstance(names, list) or len(names) not in (6, 9) or not isinstance(units, list) or len(units) != len(names):
        raise ValueError("Explicit names and units for six or nine action dimensions are required")
    dimension = len(names)
    normalization = source.get("normalization", {})
    low, high = np.asarray(normalization.get("low"), np.float32), np.asarray(normalization.get("high"), np.float32)
    if low.shape != (dimension,) or high.shape != low.shape or not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(high <= low):
        raise ValueError("Explicit finite normalization ranges are required")
    fps = source.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not np.isfinite(fps) or fps <= 0 or fps > 1e9:
        raise ValueError("Recording decision FPS is required")
    states, actions, frame_times, state_times, action_times, frames = [], [], [], [], [], []
    domains = set()
    for sample in samples:
        frame = sample.get("frame", {})
        if not isinstance(frame.get("frame_id"), str) or not frame["frame_id"]:
            raise ValueError("An immutable source camera frame ID is required")
        image_path = Path(frame.get("file", ""))
        image_path = image_path if image_path.is_absolute() else source_path.parent / image_path
        if not image_path.is_file() or sha(image_path) != frame.get("sha256"):
            raise ValueError("Station source frame checksum mismatch")
        state, action = np.asarray(sample.get("state"), np.float32), np.asarray(sample.get("action"), np.float32)
        if state.shape != (dimension,) or action.shape != state.shape or not np.isfinite(state).all() or not np.isfinite(action).all():
            raise ValueError("Station state/action dimensions or values differ")
        if np.any(state < low - 1e-3) or np.any(state > high + 1e-3) or np.any(action < low - 1e-3) or np.any(action > high + 1e-3):
            raise ValueError("Source values do not match declared normalization units")
        ft, st, at = frame.get("monotonic_ns"), sample.get("state_monotonic_ns"), sample.get("action_monotonic_ns")
        if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (ft, st, at)) or not ft <= st <= at:
            raise ValueError("Frame/state/action causal timestamp ordering differs")
        frame_domain, sample_domain = frame.get("clock_domain"), sample.get("clock_domain")
        if not frame_domain or frame_domain != sample_domain:
            raise ValueError("Frame and servo clocks cannot be silently merged")
        domains.add(frame_domain)
        frames.append((image_path, frame))
        states.append(state)
        actions.append(action)
        frame_times.append(ft)
        state_times.append(st)
        action_times.append(at)
    if len(domains) != 1 or np.any(np.diff(action_times) <= 0):
        raise ValueError("An episode must remain in one boot clock with increasing actions")
    states, actions = np.asarray(states), np.asarray(actions)
    times = {"frame_monotonic_ns": np.asarray(frame_times, np.int64),
             "state_monotonic_ns": np.asarray(state_times, np.int64),
             "action_monotonic_ns": np.asarray(action_times, np.int64)}
    capture=None
    if capture_manifest_path is not None:
        from explorer_shared.capture_timeline import select_source_samples
        path=Path(capture_manifest_path).resolve();checksum=sha(path)
        if not isinstance(expected_capture_sha256,str) or checksum!=expected_capture_sha256:
            raise ValueError('Exact immutable source capture envelope SHA required')
        if path.stat().st_size>64*1024**2:raise ValueError('Source capture envelope exceeds metadata budget')
        envelope=json.loads(path.read_bytes());lifecycle=envelope.get('lifecycle') or {}
        original_context=dict(source.get('source_context') or {});original_context.pop('native_phase_annotation',None)
        if (envelope.get('recording_id')!=episode_id or envelope.get('state')!='completed' or lifecycle.get('state')!='completed' or
                (envelope.get('original_manifest') or {}).get('sha256')!=source_digest or
                envelope.get('source_clock_domain')!=source.get('clock_domain') or envelope.get('source_boot_id')!=source.get('boot_id') or
                lifecycle.get('source_clock_domain')!=source.get('clock_domain') or lifecycle.get('source_boot_id')!=source.get('boot_id') or
                envelope.get('source_context')!=original_context or lifecycle.get('source_context')!=original_context):
            raise ValueError('Source capture timeline/original identity changed')
        selection=select_source_samples(samples,lifecycle.get('pause_intervals'),
            started_ns=lifecycle.get('started_monotonic_ns'),ended_ns=lifecycle.get('ended_monotonic_ns'),
            clock_domain=source.get('clock_domain'),boot_id=source.get('boot_id'))
        if selection!=envelope.get('sample_selection'):
            raise ValueError('Source capture sample selection changed or crossed a pause')
        capture={'path':path,'sha256':checksum,'envelope':envelope,'selection':selection}
    elif expected_capture_sha256 is not None:
        raise ValueError('Source capture SHA requires its original envelope')
    segments, gaps = _causal_segments(times["action_monotonic_ns"], fps,
        source_segment_ids=[row['recording_segment_id'] for row in capture['selection']] if capture else None)
    if source_path.is_relative_to(target) or any(path.resolve().is_relative_to(target) for path, _ in frames):
        raise ValueError("Conversion target cannot contain the original source files")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / ("." + target.name + ".conversion.lock")).open("a") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX)
        if target.exists():
            existing = _completed_conversion(target, source_digest,expected_capture_sha256)
            if existing is not None:
                return existing
            # Incomplete outputs are retained for inspection rather than overwritten.
            target.rename(target.with_name(target.name + ".incomplete-" + uuid.uuid4().hex))
        staging = Path(tempfile.mkdtemp(prefix="." + target.name + ".converting-", dir=target.parent))
        try:
            result = _write_conversion(staging, source_path, source_digest, source, frames, states,
                                       actions, times, low, high, segments, gaps, next(iter(domains)),capture=capture)
            if sha(source_path) != source_digest:
                raise ValueError("Source station manifest changed during conversion")
            if capture and sha(capture['path'])!=capture['sha256']:
                raise ValueError('Source capture envelope changed during conversion')
            write_json(staging / "manifest.json", result)
            os.rename(staging, target)
            return result
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def _causal_segments(action_times, fps, *, source_segment_ids=None):
    """Anchor each grid at its first native action; never interpolate future data."""
    period = 1e9 / fps
    if source_segment_ids is not None:
        return _capture_segments(action_times,period,source_segment_ids)
    cuts = (np.flatnonzero(np.diff(action_times) > 3 * period) + 1).tolist()
    bounds = [0, *cuts, len(action_times)]
    segments, gaps = [], []
    for start, end in zip(bounds[:-1], bounds[1:]):
        native = action_times[start:end]
        count = int(np.floor((int(native[-1]) - int(native[0])) / period)) + 1
        grid = native[0] + np.rint(np.arange(count) * period).astype(np.int64)
        grid = grid[grid <= native[-1]]
        selected = np.searchsorted(native, grid, side="right") - 1 + start
        segments.append({"start": start, "end": end, "grid": grid, "selected": selected})
        if start:
            gaps.append({"previous_source_index": start - 1, "next_source_index": start,
                         "gap_ns": int(action_times[start] - action_times[start - 1]),
                         "threshold_ns": 3 * period})
    return segments, gaps


def _capture_segments(action_times,period,source_segment_ids):
    if len(source_segment_ids)!=len(action_times):raise ValueError('Exact source segment count required')
    groups=[];current=[]
    for index,identity in enumerate(source_segment_ids):
        if identity is None:
            if current:groups.append(current);current=[]
        else:
            if current and (identity!=source_segment_ids[current[-1]] or action_times[index]-action_times[current[-1]]>3*period):
                groups.append(current);current=[]
            current.append(index)
    if current:groups.append(current)
    if not groups:raise ValueError('No samples inside retained source capture intervals')
    segments=[];gaps=[]
    for indices in groups:
        native=action_times[indices];count=int(np.floor((int(native[-1])-int(native[0]))/period))+1
        grid=native[0]+np.rint(np.arange(count)*period).astype(np.int64);grid=grid[grid<=native[-1]]
        selected=np.asarray(indices,dtype=np.int64)[np.searchsorted(native,grid,side='right')-1]
        segments.append({'start':indices[0],'end':indices[-1]+1,'grid':grid,'selected':selected,
            'recording_segment_id':source_segment_ids[indices[0]]})
        if len(segments)>1:
            previous=int(segments[-2]['selected'][-1]);following=indices[0]
            gaps.append({'previous_source_index':previous,'next_source_index':following,
                'gap_ns':int(action_times[following]-action_times[previous]),'threshold_ns':3*period,
                'cut_basis':'source_capture_pause_or_excluded_boundary' if following!=previous+1 or source_segment_ids[following]!=source_segment_ids[previous] else 'original_timestamp_gap'})
    return segments,gaps


def _completed_conversion(target, source_digest,capture_sha256=None):
    path = target / "manifest.json"
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        return None
    if not isinstance(saved, dict):
        return None
    descriptors = saved.get("episodes")
    if not isinstance(descriptors, list) or not descriptors:
        return None
    if saved.get("source_manifest_sha256") != source_digest:
        raise ValueError("Conversion target belongs to different source data; it will not be overwritten")
    if saved.get('source_capture_manifest_sha256')!=capture_sha256:
        raise ValueError('Conversion target belongs to a different capture timeline; original retained')
    artifacts = {item["file"]: item["sha256"] for item in descriptors}
    artifacts.update(saved.get("source_files_sha256", {}))
    for name, digest in artifacts.items():
        artifact = (target / name).resolve()
        if not artifact.is_relative_to(target) or not artifact.is_file() or sha(artifact) != digest:
            raise ValueError("Completed conversion artifact checksum mismatch; existing data retained")
    return saved


def _write_conversion(staging, source_path, source_digest, source, frames, states, actions,
                      times, low, high, segments, gaps, domain,*,capture=None):
    originals = staging / "source_originals"
    (originals / "frames").mkdir(parents=True)
    shutil.copyfile(source_path, originals / "manifest.json")
    if sha(originals / "manifest.json") != source_digest:
        raise ValueError("Source station manifest changed while retaining the original")
    artifacts = {"source_originals/manifest.json": source_digest}
    if capture:
        shutil.copyfile(capture['path'],originals/'capture-envelope.json')
        if sha(originals/'capture-envelope.json')!=capture['sha256']:
            raise ValueError('Source capture envelope changed while retaining original')
        artifacts['source_originals/capture-envelope.json']=capture['sha256']
    source_files, shape = [], None
    for index, (path, descriptor) in enumerate(frames):
        suffix = path.suffix.lower() if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff") else ".image"
        retained = originals / "frames" / (f"{index:06d}" + suffix)
        shutil.copyfile(path, retained)
        if sha(retained) != descriptor["sha256"] or sha(path) != descriptor["sha256"]:
            raise ValueError("Source frame changed during conversion")
        with Image.open(retained) as image:
            frame_shape = (image.height, image.width, 3)
        if shape is not None and shape != frame_shape:
            raise ValueError("Station image resolution changed within recording")
        shape = frame_shape
        relative = str(retained.relative_to(staging))
        artifacts[relative] = descriptor["sha256"]
        source_files.append({"source_path": str(path.resolve()), "retained_file": relative,
                             "sha256": descriptor["sha256"], "source_frame_id": descriptor["frame_id"]})
    native_path = originals / "native_samples.npz"
    np.savez_compressed(native_path, state_native=states, action_native=actions, **times)
    artifacts[str(native_path.relative_to(staging))] = sha(native_path)
    episode_id, split = source["episode_id"], source.get("split", "train")
    episodes, segment_metadata = [], []
    for index, segment in enumerate(segments):
        selected, grid = segment["selected"], segment["grid"]
        npz_path = staging / f"episode_{index:04d}.npz"
        _write_segment(npz_path, staging, source_files, selected, grid, shape, states, actions, times, low, high)
        identities = [frames[i][1]["frame_id"] for i in selected]
        episodes.append({"id": episode_id if len(segments) == 1 else episode_id + f"-segment-{index:04d}",
                         "source_episode_id": episode_id, "file": npz_path.name, "sha256": sha(npz_path),
                         "frames": len(selected), "split": split, "outcome": source.get("outcome"),
                         "outcome_label_source": source.get("outcome_label_source"),
                         "outcome_scope": "Original recording label; segments are not independently evaluated",
                         "clock_domain": domain, "frame_source_ids": identities,
                         "source_indices": selected.tolist(), "grid_start_monotonic_ns": int(grid[0]),
                         "grid_end_monotonic_ns": int(grid[-1])})
        segment_metadata.append({"episode_file": npz_path.name, "start_source_index": segment["start"],
                                 "end_source_index_exclusive": segment["end"],
                                 "original_count": segment["end"] - segment["start"],
                                 "converted_count": len(selected), "source_indices": selected.tolist(),
                                 "dropped_source_indices": sorted(set(range(segment["start"], segment["end"])) - set(selected.tolist()))})
        if capture:
            episodes[-1]['recording_segment_id']=segment['recording_segment_id']
            segment_metadata[-1]['recording_segment_id']=segment['recording_segment_id']
    all_selected = np.concatenate([item["selected"] for item in segments])
    all_grid = np.concatenate([item["grid"] for item in segments])
    unchanged = np.array_equal(all_selected, np.arange(len(frames))) and np.array_equal(all_grid, times["action_monotonic_ns"])
    result = {"schema_version": 1, "conversion_version": 2, "format": "explorer_robot_episode_v1",
              "dataset_id": "station-" + episode_id, "source": "station_demonstration", "created_wall_utc": utc(),
              "source_manifest_sha256": source_digest, "source_manifest_path": str(source_path),
              "source_files": source_files, "source_files_sha256": artifacts,
              "source_calibration_metadata": {key: value for key, value in source.items() if "calibrat" in key.lower()},
              "action_names": source["action_names"], "action_units": source["action_units"],
              "action_normalization": {"low": low.tolist(), "high": high.tolist()},
              "state_basis": source["state_basis"], "action_basis": source["action_basis"], "clock_domain": domain,
              "fps": source["fps"], "contains_physical_recordings": True,
              "joint_measurements_claimed": source["state_basis"] == "measured_state", "physical_success_inferred": False,
              "task": source.get("task", "Imitate the recorded robot demonstration"),
              "frame_source_ids": [frame["frame_id"] for _, frame in frames],
              "timestamp_uncertainty": "Host receive/read/dispatch timestamps; exposure and motor application time are not measured",
              "resampling": {"method": "causal_previous_sample_hold", "applied": not unchanged or len(segments) > 1,
                  "values_resampled": not unchanged, "segmented": len(segments) > 1,
                  "grid_basis": "Nominal FPS grid anchored to first native action per segment; not actual sensor capture or motor application times",
                  "hold_semantics": "Only the most recent fully dispatched past sample is selected. Reused state and command values are held estimates between native reads, not new measurements.",
                  "gap_threshold_periods": 3, "original_count": len(frames), "converted_count": len(all_selected),
                  "gaps": gaps, "segments": segment_metadata,
                  "all_native_samples_retained": "source_originals/native_samples.npz"},
              "episodes": episodes, "method_ids": ["ENGINEERING_GLUE"]}
    if capture:
        result.update(conversion_version=3,source_capture_manifest_path=str(capture['path']),
            source_capture_manifest_sha256=capture['sha256'],source_pause_selection_verified=True)
        result['resampling']['excluded_source_capture_indices']=[row['source_sample_index'] for row in capture['selection'] if not row['included_after_pause_filter']]
    return result


def _write_segment(path, staging, source_files, selected, grid, shape, states, actions, times, low, high):
    rgb_path, rgb = path.with_suffix(".rgb-memory.npy"), None
    try:
        rgb = np.lib.format.open_memmap(rgb_path, mode="w+", dtype=np.uint8, shape=(len(selected), *shape))
        for index, source_index in enumerate(selected):
            with Image.open(staging / source_files[source_index]["retained_file"]) as image:
                rgb[index] = np.asarray(image.convert("RGB"))
        rgb.flush()
        selected_states, selected_actions = states[selected], actions[selected]
        np.savez_compressed(path, rgb=rgb, state_native=selected_states, action_native=selected_actions,
                            state=2 * (selected_states - low) / (high - low) - 1,
                            action=2 * (selected_actions - low) / (high - low) - 1,
                            grid_monotonic_ns=grid, source_sample_index=selected,
                            **{name: values[selected] for name, values in times.items()})
    finally:
        if rgb is not None:
            del rgb
        rgb_path.unlink(missing_ok=True)
