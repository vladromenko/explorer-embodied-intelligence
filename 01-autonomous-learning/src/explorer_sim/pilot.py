"""Real MuJoCo CAD reach pilot: PPO teacher -> RGB official ACT student.

This is an engineering reach experiment with a simulated base fixture. It does
not establish sock manipulation, contact calibration or physical transfer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

import gymnasium as gym
import mujoco
import numpy as np
from PIL import Image

from legacy.simulator.explorer_model import (ARM_JOINTS, build_mjcf, model_metadata,
                                            resolve_mesh_dir, servo_to_joint)


ACTION_NAMES = ["base", "shoulder", "elbow", "wrist_pitch", "wrist_roll", "gripper", "vx", "vy", "wz"]
LOW = np.array([0, 0, 0, 0, 0, 30, -.2, -.2, -.6], dtype=np.float32)
HIGH = np.array([180, 180, 180, 180, 270, 170, .2, .2, .6], dtype=np.float32)
HOME = np.array([90, 90, 90, 90, 90, 30, 0, 0, 0], dtype=np.float32)
METHODS = ["PPO_SB3_2_9", "ACT_LEROBOT_0_6_1", "EXPLORER_CAD_MUJOCO", "ENGINEERING_GLUE"]


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as output:
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def completed_result(folder):
    path = folder / "result.json"
    if not path.is_file():
        return None
    result = json.loads(path.read_text())
    checks = [(folder / "teacher_ppo.zip", result["teacher"]["sha256"]),
              (folder / "dataset/manifest.json", result["dataset"]["manifest_sha256"])]
    manifest = json.loads((folder / "dataset/manifest.json").read_text())
    for descriptor in manifest["episodes"]:
        episode = (folder / "dataset" / descriptor["file"]).resolve()
        if not episode.is_relative_to((folder / "dataset").resolve()):
            raise ValueError("Invalid stored episode path")
        checks.append((episode, descriptor["sha256"]))
    for name, digest in result.get("student", {}).get("checkpoint_sha256", {}).items():
        if Path(name).name != name:
            raise ValueError("Invalid stored checkpoint filename")
        checks.append((folder / "student_act" / name, digest))
    for artifact, expected in checks:
        if not artifact.is_file() or sha(artifact) != expected:
            raise ValueError("Completed pilot artifact checksum mismatch")
    return result


def preserve_interrupted_attempt(folder):
    if (folder / "provenance.json").exists():
        archive = folder / "interrupted_attempts" / uuid.uuid4().hex
        archive.mkdir(parents=True)
        names = ["provenance.json", "frame.jpg", "frame.json", "model.xml", "teacher_ppo.zip", "dataset",
                 "act_training.json", "act_evaluation.json", "student_act", "act_optimizer.pt",
                 "lerobot_v3", "lerobot_v3-readback.json"]
        for name in names:
            if (folder / name).exists():
                shutil.move(str(folder / name), str(archive / name))


def parameters(config):
    def integer(name, default, lower, upper):
        value = config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or not lower <= value <= upper:
            raise ValueError(f"Invalid pilot parameter: {name}")
        return int(value)
    return {"seed": integer("seed", 41, 0, 1_000_000),
            "teacher_steps": integer("teacher_steps", 4096, 64, 1_000_000),
            "demonstration_episodes": integer("demonstration_episodes", 12, 1, 500),
            "evaluation_episodes": integer("evaluation_episodes", 6, 1, 100),
            "horizon": integer("horizon", 60, 8, 300),
            "act_steps": integer("act_steps", 120, 0, 100_000),
            "batch_size": integer("batch_size", 8, 1, 64),
            "image_size": integer("image_size", 64, 32, 224),
            "chunk_size": integer("chunk_size", 8, 1, 100),
            "device": config.get("device", "cpu")}


class ReachEnv(gym.Env):
    metadata = {"render_modes": ["rgb_array"], "render_fps": 12}

    def __init__(self, mesh_dir, horizon=60):
        super().__init__()
        xml = ET.fromstring(build_mjcf(mesh_dir=mesh_dir, scene="empty", verify_assets=True))
        # A task fixture, explicitly recorded in provenance; CAD geometry stays intact.
        equality = xml.find("equality")
        if equality is None:
            equality = ET.SubElement(xml, "equality")
        ET.SubElement(equality, "weld", name="reach_base_fixture", body1="base_link")
        ET.SubElement(xml.find("worldbody"), "site", name="reach_target", type="sphere",
                      pos=".05 0 .5", size=".018", rgba=".1 1 .25 .9")
        self.xml = ET.tostring(xml, encoding="unicode")
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.model.vis.global_.offwidth = 320
        self.model.vis.global_.offheight = 240
        self.data = mujoco.MjData(self.model)
        self.reference = mujoco.MjData(self.model)
        self.qids = np.array([self.model.joint(name).qposadr[0] for name in ARM_JOINTS])
        self.dids = np.array([self.model.joint(name).dofadr[0] for name in ARM_JOINTS])
        self.servo_ids = np.array([self.model.actuator(f"servo{i}").id for i in range(1, 7)])
        self.site_id = self.model.site("grasp_center").id
        self.target_id = self.model.site("reach_target").id
        self.renderer = None
        self.horizon, self.period = horizon, .08
        self.action_space = gym.spaces.Box(-1, 1, (3,), np.float32)
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, (21,), np.float32)
        self.command, self.goal = HOME.copy(), np.zeros(3)
        self.steps, self.hold = 0, 0
        self.last_distance = 0.0
        self.last_action = np.zeros(3, np.float32)

    def _pose(self, data, command):
        mujoco.mj_resetData(self.model, data)
        q = servo_to_joint(command[:6])
        data.qpos[self.qids] = q
        data.ctrl[self.servo_ids] = q
        mujoco.mj_forward(self.model, data)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.command, self.steps, self.hold = HOME.copy(), 0, 0
        self.last_action[:] = 0
        self._pose(self.data, self.command)
        for _ in range(60):
            mujoco.mj_step(self.model, self.data)
        self.goal = self.data.site_xpos[self.site_id].copy()
        attempts = 0
        while self.distance() < .04 and attempts < 32:
            candidate = HOME.copy()
            candidate[:3] += self.np_random.uniform([-18, -15, -15], [18, 15, 15])
            self._pose(self.reference, candidate)
            self.goal = self.reference.site_xpos[self.site_id].copy()
            attempts += 1
        if self.distance() < .04:
            raise RuntimeError("Could not sample a nontrivial reachable goal")
        self.model.site_pos[self.target_id] = self.goal
        mujoco.mj_forward(self.model, self.data)
        self.last_distance = self.distance()
        return self.observation(), self.info()

    def distance(self):
        return float(np.linalg.norm(self.data.site_xpos[self.site_id] - self.goal))

    def observation(self):
        return np.concatenate([self.data.qpos[self.qids], self.data.qvel[self.dids],
                               (self.goal - self.data.site_xpos[self.site_id]) * 10,
                               self.goal, self.last_action]).astype(np.float32)

    def info(self):
        distance = self.distance()
        return {"distance_m": distance, "success": self.hold >= 3, "steps": self.steps,
                "goal_xyz": self.goal.tolist(), "grasp_center_xyz": self.data.site_xpos[self.site_id].tolist(),
                "command9": self.command.tolist(), "physical_run": False}

    def step(self, action):
        action = np.asarray(action, np.float32)
        if action.shape != (3,) or not np.isfinite(action).all():
            raise ValueError("Expected three finite simulated joint increments")
        self.last_action = np.clip(action, -1, 1)
        command = self.command.copy()
        command[:3] = np.clip(command[:3] + self.last_action * 2.5, 35, 145)
        return self.step_command(command)

    def step_command(self, command):
        command = np.asarray(command, np.float32)
        if command.shape != (9,) or not np.isfinite(command).all():
            raise ValueError("Expected a finite command9")
        target = HOME.copy()
        target[:3] = np.clip(command[:3], 35, 145)
        # The pilot changes only the first three arm joints; other simulated axes hold.
        target[:3] = self.command[:3] + np.clip(target[:3] - self.command[:3], -2.5, 2.5)
        self.last_action = (target[:3] - self.command[:3]) / 2.5
        self.command = target
        self.data.ctrl[self.servo_ids] = servo_to_joint(target[:6])
        for _ in range(round(self.period / self.model.opt.timestep)):
            mujoco.mj_step(self.model, self.data)
        self.steps += 1
        distance = self.distance()
        self.hold = self.hold + 1 if distance < .025 else 0
        success = self.hold >= 3
        reward = 100 * (self.last_distance - distance) - 20 * distance + 5 * float(success)
        reward -= .01 * float(np.square(self.last_action).sum())
        self.last_distance = distance
        unstable = not np.isfinite(self.data.qpos).all() or any(warning.number for warning in self.data.warning)
        return self.observation(), float(reward), bool(success or unstable), bool(self.steps >= self.horizon), self.info()

    def render(self):
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=240, width=320)
        self.renderer.update_scene(self.data, camera="overview")
        return self.renderer.render().copy()

    def student_state(self):
        return ((self.command - LOW) / (HIGH - LOW) * 2 - 1).astype(np.float32)

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None


def write_frame(env, folder, caption=None):
    frame = Image.fromarray(env.render())
    temporary = folder / "frame.tmp.jpg"
    frame.save(temporary, quality=85)
    temporary.replace(folder / "frame.jpg")
    if caption:
        write_json(folder / "frame.json", {"wall_utc": utc(), "source": "actual_mujoco_render",
                   "caption": caption, "sim_time_s": float(env.data.time), "physical_camera": False})


def state_digest(model):
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def evaluate_teacher(policy, env, seeds, folder, caption="PPO teacher: independent simulated reach evaluation"):
    episodes = []
    for seed in seeds:
        observation, initial = env.reset(seed=seed)
        rewards, done = [], False
        while not done:
            action, _ = policy.predict(observation, deterministic=True)
            observation, reward, terminal, truncated, info = env.step(action)
            rewards.append(reward)
            done = terminal or truncated
        episodes.append({"seed": seed, "initial_distance_m": initial["distance_m"], **info,
                         "return": float(sum(rewards))})
        write_frame(env, folder, caption)
    return summarize(episodes)


def summarize(episodes):
    return {"episodes": episodes, "n": len(episodes),
            "successes": sum(item["success"] for item in episodes),
            "success_rate": sum(item["success"] for item in episodes) / max(1, len(episodes)),
            "mean_final_distance_m": float(np.mean([item["distance_m"] for item in episodes])),
            "physical_run_ids": [], "evaluation_domain": "simulation", "independent_from_training_episodes": True,
            "split_role": "engineering_development_evaluation", "sealed_final_test": False}


def collect_dataset(policy, env, folder, settings, update):
    directory = folder / "dataset"
    directory.mkdir(exist_ok=True)
    episodes = []
    for index in range(settings["demonstration_episodes"]):
        seed = settings["seed"] + 2000 + index
        observation, initial = env.reset(seed=seed)
        images, states, raw_states, actions, actions9, rewards, targets, sim_times = [], [], [], [], [], [], [], []
        done = False
        while not done:
            image = Image.fromarray(env.render()).resize((settings["image_size"], settings["image_size"]))
            images.append(np.asarray(image, np.uint8))
            states.append(env.student_state())
            raw_states.append(env.command.copy())
            sim_times.append(float(env.data.time))
            targets.append(env.goal.copy())  # evaluator-only, deliberately excluded from ACT input
            action, _ = policy.predict(observation, deterministic=True)
            observation, reward, terminal, truncated, info = env.step(action)
            actions9.append(env.command.copy())
            actions.append(((env.command - LOW) / (HIGH - LOW) * 2 - 1).astype(np.float32))
            rewards.append(reward)
            done = terminal or truncated
        path = directory / f"episode_{index:04d}.npz"
        np.savez_compressed(path, rgb=np.asarray(images), state=np.asarray(states), action=np.asarray(actions),
                            state_command9=np.asarray(raw_states), command9=np.asarray(actions9), reward=np.asarray(rewards),
                            evaluator_goal_xyz=np.asarray(targets), simulator_time_s=np.asarray(sim_times))
        episodes.append({"id": f"episode_{index:04d}", "seed": seed, "file": path.name,
                         "sha256": sha(path), "frames": len(images), "outcome": info,
                         "split": "imitation_train", "quality": "ppo_rollout_outcome_labelled"})
        update(.45 + .15 * (index + 1) / settings["demonstration_episodes"], "collecting_teacher_rollouts")
        write_frame(env, folder, "Actual CAD robot teacher rollout; outcomes recorded")
    manifest = {"schema_version": 1, "dataset_id": "cad-reach-" + folder.name,
                "source": "real_mujoco_ppo_rollouts", "format": "explorer_npz_episode_v1",
                "official_lerobot_dataset_format": False, "created_wall_utc": utc(),
                "fps": 1 / env.period, "decision_period_s": env.period,
                "task": "Reach the visible green target with the gripper; base fixed",
                "episodes": episodes, "action_names": ACTION_NAMES,
                "actor_inputs": ["rgb", "state_command_estimate"],
                "teacher_and_evaluator_only": ["evaluator_goal_xyz", "simulator_joint_state"],
                "action_normalization": {"low": LOW.tolist(), "high": HIGH.tolist()},
                "physics_model": "CAD-derived ROSMASTER M3 Pro; fixed simulated base reach",
                "contains_physical_recordings": False}
    write_json(directory / "manifest.json", manifest)
    return manifest


def export_dataset_if_available(folder):
    try:
        from .dataset_export import export_lerobot_v3
        return export_lerobot_v3(folder / "dataset/manifest.json", folder / "lerobot_v3")
    except ImportError as exc:
        return {"status": "dependency_blocked", "official_readback_passed": False, "error": str(exc)}


def run_pilot(store, job, root):
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    import torch

    settings = parameters(job.get("config") or {})
    if settings["device"] not in ("cpu", "cuda"):
        raise ValueError("Pilot device must be cpu or cuda")
    if settings["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    torch.set_num_threads(4)
    folder = store.root / "jobs" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    saved = completed_result(folder)
    if saved is not None:
        if not saved["dataset"].get("official_export", {}).get("official_readback_passed"):
            saved["dataset"]["official_export"] = export_dataset_if_available(folder)
            write_json(folder / "result.json", saved)
        card = saved.get("skill_card")
        if card and not any(item["id"] == card["id"] for item in store.skills()):
            store.add_skill(card)
        return saved
    preserve_interrupted_attempt(folder)
    assets = resolve_mesh_dir(os.environ.get("EXPLORER_SIM_MESH_DIR") or
                              Path(root) / "_registry/private/assets/m3_reference/meshes")
    started = time.monotonic()
    versions = {name: importlib.metadata.version(name) for name in ("mujoco", "stable_baselines3", "torch", "numpy")}
    provenance = {"schema_version": 1, "created_wall_utc": utc(), "settings": settings,
                  "method_ids": METHODS, "versions": versions, "task": "engineering_fixed_base_reach_pilot",
                  "physical_io": False, "cad_mesh_count": 20,
                  "cad_mesh_sha256": {path.name: sha(path) for path in sorted(assets.glob("*.STL"))},
                  "legacy_model_source_sha256": sha(Path(__file__).parents[1] / "legacy/simulator/explorer_model.py"),
                  "pilot_source_sha256": sha(Path(__file__)),
                  "student_source_sha256": sha(Path(__file__).with_name("student.py")),
                  "model_metadata": model_metadata(), "model_fixture": "base_link welded to world in simulation",
                  "training_seed": settings["seed"],
                  "independent_evaluation_seeds": [settings["seed"] + 10000 + i for i in range(settings["evaluation_episodes"])],
                  "not_validated": ["sock_manipulation", "real_contact_physics", "physical_transfer", "mobile_navigation"]}
    write_json(folder / "provenance.json", provenance)

    def update(progress, phase):
        store.update_job(job["id"], progress=progress, result={"phase": phase, "engineering_pilot": True})

    env, evaluation_env = ReachEnv(assets, settings["horizon"]), ReachEnv(assets, settings["horizon"])
    result = {"task": "engineering_fixed_base_reach_pilot", "engineering_pilot": True,
              "physical_io": False, "method_ids": METHODS, "artifacts_dir": str(folder)}
    try:
        observation, _ = env.reset(seed=settings["seed"])
        write_frame(env, folder, "Checksum-verified CAD ROSMASTER: actual MuJoCo reach scene")
        (folder / "model.xml").write_text(env.xml)

        class NoMotion:
            def predict(self, observation, deterministic=True):
                return np.zeros(3, np.float32), None

        result["no_motion_baseline"] = evaluate_teacher(NoMotion(), evaluation_env,
            provenance["independent_evaluation_seeds"], folder, "No-motion baseline in the same CAD reach scenes")
        policy = PPO("MlpPolicy", env, seed=settings["seed"], device="cpu", n_steps=128,
                     batch_size=64, n_epochs=5, learning_rate=3e-4,
                     policy_kwargs={"net_arch": [64, 64]}, verbose=0)
        initial_digest = state_digest(policy.policy)

        class Progress(BaseCallback):
            def _on_step(self):
                if self.num_timesteps % 128 == 0:
                    update(.05 + .35 * min(1, self.num_timesteps / settings["teacher_steps"]), "optimizing_real_ppo_teacher")
                    write_frame(env, folder, f"Actual PPO optimization: {self.num_timesteps} simulator steps")
                return True

        update(.02, "optimizing_real_ppo_teacher")
        policy.learn(total_timesteps=settings["teacher_steps"], callback=Progress())
        teacher_path = folder / "teacher_ppo.zip"
        policy.save(teacher_path)
        reloaded = PPO.load(teacher_path, device="cpu")
        check_observation, _ = evaluation_env.reset(seed=settings["seed"] + 9999)
        before_action, _ = policy.predict(check_observation, deterministic=True)
        after_action, _ = reloaded.predict(check_observation, deterministic=True)
        reloaded_ok = bool(np.allclose(before_action, after_action, atol=1e-7))
        if not reloaded_ok:
            raise RuntimeError("PPO checkpoint reload mismatch")
        teacher_evaluation = evaluate_teacher(reloaded, evaluation_env, provenance["independent_evaluation_seeds"], folder)
        result["teacher"] = {"algorithm": "Stable-Baselines3 PPO", "privileged_state": True,
                             "device": "cpu",
                             "environment_timesteps": int(policy.num_timesteps),
                             "optimizer_updates": int(policy._n_updates),
                             "weights_changed": initial_digest != state_digest(policy.policy),
                             "checkpoint": str(teacher_path), "sha256": sha(teacher_path),
                             "save_reload_passed": reloaded_ok, "evaluation": teacher_evaluation}
        dataset = collect_dataset(reloaded, env, folder, settings, update)
        result["dataset"] = {"id": dataset["dataset_id"], "manifest_sha256": sha(folder / "dataset/manifest.json"),
                             "episode_count": len(dataset["episodes"]),
                             "successful_teacher_episodes": sum(item["outcome"]["success"] for item in dataset["episodes"])}
        result["dataset"]["official_export"] = export_dataset_if_available(folder)
        if settings["act_steps"] > 0:
            from .student import train_and_evaluate_act
            try:
                result["student"] = train_and_evaluate_act(env, evaluation_env, folder, settings, dataset,
                                                          provenance["independent_evaluation_seeds"], update)
            except ImportError as exc:
                result["student"] = {"status": "dependency_blocked", "algorithm": "official LeRobot ACT",
                                     "error": str(exc), "trained": False}
        else:
            result["student"] = {"status": "not_requested", "algorithm": "official LeRobot ACT", "trained": False}
        result.update(elapsed_s=time.monotonic() - started, provenance_sha256=sha(folder / "provenance.json"))
        student = result["student"]
        evaluated = student.get("trained", False)
        card = {"id": "sim-reach-" + job["id"], "task": result["task"],
                "source_type": "sim_teacher_distilled_act" if evaluated else "sim_teacher_ppo",
                "status": "simulation_pilot_evaluated", "action_contract": {"version": 1, "names": ACTION_NAMES,
                    "arm_unit": "degrees", "base_units": ["m/s", "m/s", "rad/s"], "pilot_active_indices": [0, 1, 2]},
                "lineage": {"job_id": job["id"], "dataset_id": dataset["dataset_id"], "methods": METHODS,
                            "teacher_sha256": result["teacher"]["sha256"], "provenance_sha256": result["provenance_sha256"]},
                "evaluation": student["evaluation"] if evaluated else teacher_evaluation,
                "limitations": ["Engineering reach pilot, not a sock task", "Simulated fixed base; three active joints",
                                "Estimated actuator/contact physics", "No physical validation", "No calibrated real camera transfer"]}
        if result["teacher"]["weights_changed"]:
            result["skill_card"] = card
        write_json(folder / "result.json", result)
        if result.get("skill_card") and not any(item["id"] == card["id"] for item in store.skills()):
            store.add_skill(card)
        update(.98, "simulated_reach_pilot_evaluated")
        return result
    finally:
        env.close()
        evaluation_env.close()
