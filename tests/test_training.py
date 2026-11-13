"""Technical contract tests: synthetic images/masks are never robot/SAM evidence."""
import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from so101_lab.annotations import AnnotationError, annotate_dataset, load_frame_annotations
from so101_lab.features import FeatureContract, object_state, sha256_file, validate_raw_frame
from so101_lab.training import RawEpisodeDataset, train


class FixtureSegmenter:
    backend = "technical_test"
    provenance = {"backend": "technical_test", "warning": "Synthetic mask; not SAM inference"}
    def segment(self, rgb):
        mask = rgb[:, :, 0] > 200
        ys, xs = np.nonzero(mask)
        return [{"category": "cube", "score": .9, "bbox_xyxy": [float(xs.min()), float(ys.min()), float(xs.max()+1), float(ys.max()+1)], "mask": mask}]


def fixture_dataset(root: Path, *, episodes=2):
    root.mkdir(parents=True)
    (root / "dataset.json").write_text(json.dumps({"source": "synthetic_test", "task": "technical red cube", "calibration": {"valid": False, "source": "synthetic_test"}}))
    for ei in range(episodes):
        ep = root / "episodes" / f"ep{ei:03d}"
        ep.mkdir(parents=True)
        (ep / "episode.json").write_text(json.dumps({"status": "complete", "source": "synthetic_test", "session_id": f"fixture_session_{ei}", "task": "Move red cube"}))
        rows = []
        for fi in range(3):
            ts = 1_000_000_000 + fi * 33_333_333
            row = {"frame_id": f"{fi:08d}", "timestamp_ns": ts, "observation_timestamp_ns": ts,
                   "state_timestamp_ns": ts, "action_timestamp_ns": ts+100,
                   "observation.state": [ei+fi*.1]*6, "action": [ei+fi*.2]*6,
                   "state_valid": False, "action_valid": False, "source": "synthetic_test", "cameras": {}}
            for cam in ("top", "wrist"):
                img_path = ep / "images" / cam / f"{fi:08d}.png"
                img_path.parent.mkdir(parents=True, exist_ok=True)
                image = np.zeros((32, 32, 3), np.uint8)
                image[5:13, 4+fi:12+fi, 0] = 255
                Image.fromarray(image).save(img_path)
                row["cameras"][cam] = {"timestamp_ns": ts-10, "valid": True,
                   "path": img_path.relative_to(ep).as_posix(), "sha256": sha256_file(img_path)}
            rows.append(row)
        (ep / "frames.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    annotate_dataset(root, {"allow_test": True, "_test_segmenter": FixtureSegmenter()})
    return root


def test_numeric_features_causal_and_objects_change_input(tmp_path):
    dataset_path = fixture_dataset(tmp_path / "data")
    ep = dataset_path / "episodes/ep000"
    row = json.loads((ep / "frames.jsonl").read_text().splitlines()[0])
    ann = load_frame_annotations(ep, row, allow_test=True)
    contract = FeatureContract()
    vector = object_state(row, ann, contract, allow_test=True)
    assert len(vector) == 32
    changed = copy.deepcopy(ann)
    changed["cameras"]["top"]["objects"][0]["center_xy"][0] += 2
    assert vector != object_state(row, changed, contract, allow_test=True)
    poisoned = copy.deepcopy(row)
    poisoned.update(action=[999999]*6, phase="future", success=True, done=True)
    assert object_state(poisoned, ann, contract, allow_test=True) == vector
    changed = copy.deepcopy(ann)
    changed["cameras"]["top"]["objects"][0]["tracking"]["id"] = "different_nonordinal_id"
    assert object_state(row, changed, contract, allow_test=True) == vector
    changed["cameras"]["top"]["timestamp_ns"] += 1
    with pytest.raises(ValueError, match="Stale"):
        object_state(row, changed, contract, allow_test=True)
    with pytest.raises(ValueError, match="SAM3"):
        object_state(row, ann, contract)


def test_wrist_colour_object_features_are_causal_and_normalized():
    image = np.zeros((100, 200, 3), np.uint8)
    image[20:60, 50:110] = [0, 0, 255]  # RGB blue
    frame = {"observation.state": [1, 2, 3, 4, 5, 6]}
    contract = FeatureContract(mode="wrist_color_object",
                               camera_mode="wrist_only_experimental", target_color="blue")
    state = object_state(frame, None, contract, images={"wrist": image})
    assert contract.state_dim == 12
    assert state[:6] == [1, 2, 3, 4, 5, 6]
    assert state[6] == 1.0
    assert state[7:11] == pytest.approx([.4, .4, .3, .4], abs=.01)
    missing = object_state(frame, None, contract, images={"wrist": np.zeros_like(image)})
    assert missing[6:] == [0.0] * 6


def test_split_stats_and_future_fail_closed(tmp_path):
    path = fixture_dataset(tmp_path / "data")
    ds = RawEpisodeDataset(path, FeatureContract(), chunk_size=4, allow_test_data=True)
    train_indices, val_indices, split = ds.split(seed=42)
    assert split["unit"] == "session"
    assert not set(split["train"]) & set(split["validation"])
    assert {ds.samples[i][0] for i in train_indices}.isdisjoint({ds.samples[i][0] for i in val_indices})
    stats = ds.stats(train_indices)
    assert stats["observation.state"]["mean"][6:].tolist() == [0.0]*26
    assert stats["observation.state"]["std"][6:].tolist() == [1.0]*26
    sample = ds[2]
    assert sample["action"].shape == (4, 6)
    assert sample["action_is_pad"].tolist() == [False, True, True, True]
    with pytest.raises(ValueError, match="Тестовый"):
        RawEpisodeDataset(path, FeatureContract())
    row = copy.deepcopy(ds.episodes[0]["rows"][0])
    row["cameras"]["top"]["timestamp_ns"] = row["timestamp_ns"]+1
    with pytest.raises(ValueError, match="будущего"):
        validate_raw_frame(row, FeatureContract(), allow_test=True)


def test_missing_and_stale_annotations_rejected(tmp_path):
    path = fixture_dataset(tmp_path / "data")
    ann_path = path / "episodes/ep000/annotations/sam3-v1/frames.jsonl"
    rows = [json.loads(l) for l in ann_path.read_text().splitlines()]
    rows[0]["cameras"]["top"]["timestamp_ns"] += 1
    ann_path.write_text("".join(json.dumps(r)+"\n" for r in rows))
    with pytest.raises(AnnotationError, match="Stale"):
        RawEpisodeDataset(path, FeatureContract(), allow_test_data=True)


def test_real_smolvla_tiny_update_checkpoint_reload(tmp_path):
    import torch
    from so101_lab.inference import load_checkpoint
    path = fixture_dataset(tmp_path / "data")
    options = {"tiny_model": True, "allow_test_data": True, "device": "cpu", "mode": "sam_objects", "steps": 2, "chunk_size": 2,
               "batch_size": 1, "image_size": 32, "eval_every": 1, "save_every": 100, "seed": 7}
    result = train(path, tmp_path / "model", options)
    assert result["status"] == "completed" and np.isfinite(result["loss"])
    assert result["state_projection_update_max_abs"] > 0
    checkpoint = Path(result["checkpoint"])
    assert (checkpoint / "training_state/state.pt").is_file()
    assert (checkpoint / "pretrained_model/policy_preprocessor.json").is_file()
    with pytest.raises(ValueError, match="технический"):
        load_checkpoint(checkpoint)
    first = load_checkpoint(checkpoint, allow_technical=True)
    second = load_checkpoint(checkpoint, allow_technical=True)
    ep = path / "episodes/ep000"
    row = json.loads((ep / "frames.jsonl").read_text().splitlines()[0])
    ann = load_frame_annotations(ep, row, allow_test=True)
    images = {cam: np.asarray(Image.open(ep / raw["path"]).convert("RGB")) for cam, raw in row["cameras"].items()}
    noise = torch.zeros((1, first.policy.config.chunk_size, first.policy.config.max_action_dim))
    a = first.predict(row, images, ann, "Move red cube", noise=noise, allow_test=True)
    b = second.predict(row, images, ann, "Move red cube", noise=noise, allow_test=True)
    assert np.allclose(a, b, atol=1e-7)
    original = first.prepare_observation(row, images, ann, "Move red cube", allow_test=True)
    changed = copy.deepcopy(ann)
    changed["cameras"]["top"]["objects"][0]["center_xy"][0] += 4
    altered = first.prepare_observation(row, images, changed, "Move red cube", allow_test=True)
    # Actual state projection has a nonzero SAM feature contribution.
    with torch.no_grad():
        before = first.policy.model.state_proj(first.policy.prepare_state(original))
        after = first.policy.model.state_proj(first.policy.prepare_state(altered))
    assert not torch.allclose(before, after, atol=1e-8)
    fixed_time = torch.tensor([.5])
    loss_a, _ = first.policy(original | {"action": torch.zeros(1, 2, 6)}, noise=noise, time=fixed_time)
    loss_b, _ = first.policy(altered | {"action": torch.zeros(1, 2, 6)}, noise=noise, time=fixed_time)
    assert float((loss_a-loss_b).abs().detach()) > 0
    # Exact resume from optimizer/scheduler/RNG with unchanged dataset.
    del first, second
    resumed = train(path, tmp_path / "resume", options | {"steps": 3, "resume_from": str(checkpoint)})
    assert resumed["step"] == 3 and resumed["status"] == "completed"
