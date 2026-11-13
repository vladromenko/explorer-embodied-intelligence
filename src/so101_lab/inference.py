"""Load exactly the saved policy+SAM adapter+processors. No motion fallback.

Live physical execution is deliberately a separate explicit-start worker. This
module can also run read-only recorded-frame inference for software validation.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .features import FeatureContract, object_state, sha256_file, validate_raw_frame

MAX_OBSERVATION_AGE_MS = 2000


class _OpticalBBoxTracker:
    """Track one SAM-initialized object without running a model in the control loop."""
    def __init__(self,rgb,bbox):
        import cv2
        import numpy as np
        self.cv2=cv2;self.np=np
        self.bbox=np.asarray(bbox,float)
        self.gray=cv2.cvtColor(np.asarray(rgb),cv2.COLOR_RGB2GRAY)
        self.points=self._points(self.gray)

    def _points(self,gray):
        mask=self.np.zeros(gray.shape,self.np.uint8)
        x1,y1,x2,y2=self.bbox.astype(int)
        x1=max(0,x1);y1=max(0,y1);x2=min(gray.shape[1],x2);y2=min(gray.shape[0],y2)
        if x2>x1 and y2>y1:mask[y1:y2,x1:x2]=255
        points=self.cv2.goodFeaturesToTrack(gray,80,.01,4,mask=mask)
        if points is None:
            points=self.np.asarray([[[x1,y1]],[[x2,y1]],[[x2,y2]],[[x1,y2]]],self.np.float32)
        return points.astype(self.np.float32)

    def reset(self,rgb,bbox):
        self.bbox=self.np.asarray(bbox,float)
        self.gray=self.cv2.cvtColor(self.np.asarray(rgb),self.cv2.COLOR_RGB2GRAY)
        self.points=self._points(self.gray)

    def update(self,rgb):
        gray=self.cv2.cvtColor(self.np.asarray(rgb),self.cv2.COLOR_RGB2GRAY)
        following,status,_=self.cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None,
            winSize=(31,31),maxLevel=3,
            criteria=(self.cv2.TERM_CRITERIA_EPS|self.cv2.TERM_CRITERIA_COUNT,20,.01))
        valid=status.reshape(-1).astype(bool) if status is not None else self.np.zeros(len(self.points),bool)
        if following is not None and int(valid.sum())>=4:
            before=self.points.reshape(-1,2)[valid];after=following.reshape(-1,2)[valid]
            matrix,_=self.cv2.estimateAffinePartial2D(before,after,method=self.cv2.RANSAC)
            if matrix is not None:
                x1,y1,x2,y2=self.bbox
                corners=self.np.asarray([[x1,y1],[x2,y1],[x2,y2],[x1,y2]],float)
                moved=corners@matrix[:,:2].T+matrix[:,2]
                self.bbox=self.np.asarray([moved[:,0].min(),moved[:,1].min(),moved[:,0].max(),moved[:,1].max()])
                self.points=after.reshape(-1,1,2).astype(self.np.float32)
        self.gray=gray
        h,w=gray.shape;self.bbox[[0,2]]=self.np.clip(self.bbox[[0,2]],0,w-1);self.bbox[[1,3]]=self.np.clip(self.bbox[[1,3]],0,h-1)
        x1,y1,x2,y2=self.bbox
        if x2-x1<5 or y2-y1<5:return [0.0]*6
        if len(self.points)<12:self.points=self._points(gray)
        bw=x2-x1;bh=y2-y1
        return [1.0,(x1+x2)/(2*w),(y1+y2)/(2*h),bw/w,bh/h,min(1.0,bw*bh/(w*h)*20)]


@dataclass
class LoadedPolicy:
    policy: object
    preprocessor: object
    postprocessor: object
    contract: FeatureContract
    checkpoint: Path
    technical_test: bool
    device: str
    annotation_provenance: dict | None = None
    control_hz: float = 30.0
    episode_steps: int = 300

    def prepare_observation(self, frame: dict, images: dict, annotation: dict | None, task: str, *, allow_test=False):
        import numpy as np
        import torch
        validate_raw_frame(frame, self.contract, allow_test=allow_test)
        if self.contract.mode == "sam_objects":
            from .annotations import validate_frame_annotations,validate_annotation_provenance
            validate_frame_annotations(annotation, frame, require_cameras=self.contract.cameras,
                                       allow_test=allow_test, verify_masks=False)
            validate_annotation_provenance(annotation.get("provenance"),allow_test=allow_test,expected=self.annotation_provenance)
        state = object_state(frame, annotation, self.contract, images=images, allow_test=allow_test)
        batch = {"observation.state": torch.tensor(state, dtype=torch.float32), "task": task}
        for cam in self.contract.cameras:
            image = np.asarray(images[cam])
            if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                raise ValueError("Inference requires original uint8 RGB HWC input")
            from .policies import image_tensor
            size=self.policy.config.input_features[f"observation.images.{cam}"].shape[-1] if self.policy.name=="act" else None
            batch[f"observation.images.{cam}"] = image_tensor(image,size=size)
        return self.preprocessor(batch)

    def reset_actions(self):
        self.policy.reset()

    def predict(self, frame, images, annotation=None, task="", *, noise=None, allow_test=False, reset_queue=False):
        import torch
        if self.technical_test and not allow_test:
            raise ValueError("Технический checkpoint не разрешён для аппаратного inference")
        prepared = self.prepare_observation(frame, images, annotation, task, allow_test=allow_test)
        with torch.inference_mode():
            if reset_queue:
                self.policy.reset()
            normalized = self.policy.select_action(prepared) if self.policy.name=="act" else self.policy.select_action(prepared, noise=noise)
            action = self.postprocessor(normalized).detach().cpu()
        if action.shape != (1, 6) or not torch.isfinite(action).all():
            raise RuntimeError("Policy returned nonfinite/invalid action")
        return action[0].tolist()


def load_checkpoint(path: Path, device="cpu", *, allow_technical=False, resource_check=True, allow_untrained=False) -> LoadedPolicy:
    path = Path(path).resolve()
    if (path / "pretrained_model").is_dir():
        folder = path / "pretrained_model"
        checkpoint = path
    else:
        folder = path
        checkpoint = path.parent
    manifest = json.loads((checkpoint / "checkpoint.json").read_text())
    if manifest.get("status") != "complete":
        raise ValueError("Checkpoint is incomplete")
    if int(manifest.get("step", 0)) < 1 and not allow_untrained:
        raise ValueError("Checkpoint ещё не содержит ни одного training update; разрешён только resume")
    technical = bool(manifest.get("technical_test"))
    if technical and not allow_technical:
        raise ValueError("Это технический checkpoint, не обученная политика для робота")
    contract = FeatureContract.from_dict(json.loads((folder / "object_adapter.json").read_text()))
    if contract.digest != manifest["feature_sha256"]:
        raise ValueError("Object adapter hash differs from trained checkpoint")
    if sha256_file(folder / "model.safetensors") != manifest["model_sha256"]:
        raise ValueError("Policy weights checksum mismatch")
    files=manifest.get('files_sha256')
    if not files and not technical:raise ValueError('Checkpoint не содержит integrity manifest processors/config; требуется повторное сохранение')
    for relative,digest in (files or {}).items():
        component=(folder/relative).resolve()
        if not component.is_relative_to(folder) or sha256_file(component)!=digest:raise ValueError('Checkpoint component checksum mismatch: '+relative)
    if resource_check:
        from .resources import assert_compute_available
        assert_compute_available(device, minimum_available_gb=.6 if technical else 5.0)
    # All model and tokenizer assets local; no HF credentials or download needed.
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from .policies import components
    from lerobot.processor import PolicyProcessorPipeline
    from lerobot.processor.converters import policy_action_to_transition, transition_to_policy_action
    kind=json.loads((folder/'config.json').read_text()).get('type','smolvla')
    Config,Policy,_=components(kind)
    cfg=Config.from_pretrained(folder,local_files_only=True);cfg.device=device
    # Preserve the shorter execution horizon stored in the checkpoint.  It is
    # intentionally allowed to be smaller than the predicted action chunk.
    overrides={'device_processor':{'device':device}}
    if kind=='smolvla':
        cfg.vlm_model_name=str(folder/'backbone');cfg.load_vlm_weights=False;cfg.rtc_config=None
        overrides['tokenizer_processor']={'tokenizer_name':str(folder/'backbone')}
    else:cfg.pretrained_backbone_weights=None
    if str(device).startswith("cuda"):
        import torch
        torch.set_float32_matmul_precision("high")  # TF32 tensor cores на Orin: быстрее live-вывод, влияние на точность пренебрежимо
    policy=Policy.from_pretrained(folder,config=cfg,local_files_only=True,strict=True)
    pre=PolicyProcessorPipeline.from_pretrained(folder,config_filename='policy_preprocessor.json',local_files_only=True,overrides=overrides)
    post = PolicyProcessorPipeline.from_pretrained(folder, config_filename="policy_postprocessor.json", local_files_only=True,
             to_transition=policy_action_to_transition, to_output=transition_to_policy_action)
    policy.eval()
    policy.reset()
    lineage=json.loads((folder/"lineage.json").read_text())
    control_hz,episode_steps=_recording_profile(lineage)
    return LoadedPolicy(policy, pre, post, contract, checkpoint, technical, device,
                        lineage.get("annotation_provenance"),control_hz,episode_steps)


def _recording_profile(lineage):
    """Recover execution cadence and one-attempt duration from train recordings.

    ACT has no learned ``done`` output.  Using the longest demonstration as a
    rollout bound makes a policy that has already completed (or missed) begin a
    second attempt.  The median is the representative duration of one recorded
    attempt and is robust to a few unusually long operator episodes.
    """
    import statistics
    dataset=Path(lineage.get("dataset_path", ""))
    split=lineage.get("split", {})
    episode_ids=split.get("train", []) if isinstance(split,dict) else []
    profiles=[]
    for episode_id in episode_ids:
        try:
            metadata=json.loads((dataset/"episodes"/episode_id/"episode.json").read_text())
            profiles.append((float(metadata["fps_requested"]),int(metadata.get("frames") or metadata["frame_count"])))
        except (OSError,ValueError,TypeError,KeyError):
            pass
    if not profiles:
        return 30.0,300
    rates={rate for rate,_ in profiles}
    if len(rates)!=1 or not all(1<=rate<=120 for rate in rates):
        raise ValueError("Train episodes have inconsistent/invalid control rates")
    return rates.pop(),round(statistics.median(frames for _,frames in profiles))


def _load_segmenter(loaded):
    if loaded.contract.mode != "sam_objects":
        return None
    from .annotations import SAM3Segmenter, validate_annotation_provenance
    provenance = loaded.annotation_provenance or {}
    if provenance.get("backend") != "sam3":
        raise ValueError("Inference requires trained real SAM3 provenance")
    segmenter = SAM3Segmenter({"device": loaded.device,
        "checkpoint_path": provenance.get("checkpoint_path"), "prompts": provenance.get("prompts"),
        "confidence_threshold": provenance.get("confidence_threshold", .5)}).load()
    validate_annotation_provenance(segmenter.provenance, expected=provenance)
    return segmenter


def benchmark_observation(loaded, frame, images, task, segmenter=None, *, stop=None):
    """A frozen recorded observation, two actual model forwards, zero device I/O.

    First pass includes warmup; second is a latency estimate, not proof of live
    control or grasp quality. Original timestamps/hashes stay unchanged.
    """
    samples = []
    for index in range(2):
        if stop and stop():return {"status": "stopped", "samples": samples, "physical_actions": False}
        before = time.monotonic()
        annotation = segmenter.annotate_observation(frame, images, {}) if segmenter else None
        after_sam = time.monotonic()
        if stop and stop():return {"status": "stopped", "samples": samples, "physical_actions": False}
        action = loaded.predict(frame, images, annotation, task, reset_queue=True)
        ended = time.monotonic()
        samples.append({"warmup": index == 0, "sam_ms": (after_sam-before)*1000,
                        "policy_ms": (ended-after_sam)*1000, "total_ms": (ended-before)*1000})
    ready = samples[-1]["total_ms"] < MAX_OBSERVATION_AGE_MS
    return {"status": "PASS" if ready else "LATENCY_EXCEEDS_LIMIT", "samples": samples,
            "observation_age_limit_ms": MAX_OBSERVATION_AGE_MS, "latency_within_limit": ready,
            "physical_actions": False, "last_prediction": action,
            "limitations": "Архивный кадр: без USB/torque. Это оценка задержки моделей; live USB/STOP и качество захвата проверяются оператором"}


def check_checkpoint(path, options, progress=None, stop=None):
    """UI load check also runs the full SAM→policy chain on archived raw, if present."""
    from .resources import ROOT, ResourceLease
    from .features import safe_child
    device = options.get("device", "cuda")
    with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
        loaded = load_checkpoint(path, device=device)
        result = {"checkpoint": str(path), "loaded": True, "contract": loaded.contract.to_dict(),
                  "physical_actions": False}
        manifest=loaded.checkpoint/'checkpoint.json'
        if manifest.is_file():result['checkpoint_manifest_sha256']=sha256_file(manifest)
        if stop and stop():return {**result, "status": "stopped"}
        folder = loaded.checkpoint / "pretrained_model"
        lineage = json.loads((folder / "lineage.json").read_text())
        dataset = Path(lineage.get("dataset_path", "")).resolve()
        if not dataset.is_relative_to(ROOT / "data") or not dataset.is_dir():
            return {**result, "latency_check": {"status": "NOT_TESTED", "reason": "Исходные raw-данные недоступны. Веса загружены; для проверки задержки нужен сохранённый эпизод"}}
        episode_id = (lineage["split"].get("validation") or lineage["split"]["train"])[0]
        episode = safe_child(dataset / "episodes", episode_id)
        frames_file = episode / "frames.jsonl"
        expected = lineage["dataset_hashes"].get(str(frames_file.relative_to(dataset)))
        if expected != sha256_file(frames_file):raise ValueError("Raw-эпизод изменился после обучения; проверка checkpoint остановлена")
        frame = None
        rejected = 0
        for line in frames_file.read_text().splitlines():
            if line.strip():
                candidate = json.loads(line)
                try:validate_raw_frame(candidate, loaded.contract)
                except (ValueError,KeyError,TypeError):rejected += 1
                else:
                    frame = candidate
                    break
        if frame is None:raise ValueError('В выбранном эпизоде нет пригодного observation для проверки политики')
        result['sample_frame_id']=frame['frame_id']
        result['rejected_observations_before_sample']=rejected
        import numpy as np
        from PIL import Image
        images = {}
        for camera in loaded.contract.cameras:
            source = safe_child(episode, frame["cameras"][camera]["path"])
            if sha256_file(source) != frame["cameras"][camera]["sha256"]:raise ValueError("Raw image hash mismatch")
            with Image.open(source) as image:images[camera] = np.array(image.convert("RGB"), copy=True)
        if progress:progress({"state": "checking_inference", "message": "Проверяем SAM → политику на сохранённом кадре. Руки не открываются"})
        segmenter = _load_segmenter(loaded)
        task = options.get("task") or json.loads((episode / "episode.json").read_text()).get("task", "")
        result["latency_check"] = benchmark_observation(loaded, frame, images, task, segmenter, stop=stop)
        return result


def demo_start_pose(lineage):
    """Median first-frame joint command of the train demonstrations (the recorded rest pose)."""
    import statistics
    from .features import action_state
    dataset=Path(lineage.get("dataset_path",""))
    poses=[]
    for episode_id in (lineage.get("split",{}) or {}).get("train",[]):
        try:
            line=next(l for l in (dataset/"episodes"/episode_id/"frames.jsonl").read_text().splitlines() if l.strip())
            poses.append([float(v) for v in action_state(json.loads(line))])
        except (OSError,StopIteration,ValueError,KeyError,TypeError):
            pass
    if len(poses)<3:
        return None
    return [statistics.median(p[i] for p in poses) for i in range(6)]


def move_to_start_pose(target, progress=None, stop=None, *, tolerance=3.0, timeout_s=30.0):
    """Slowly bring the follower to the demonstrations' rest pose before the policy starts.

    Every demonstration begins from the same rest pose (wrist_roll about -90 deg).  The
    stock rollout only returns the arm to wherever the previous run left it, and a policy
    started from an unseen wrist_roll never recovers.  Uses HardwareSource's per-step and
    speed limits; no cameras, no leader.  Torque is released again on return.
    """
    from .hardware import HardwareSource, hardware_settings
    moved=False
    # A single bad status packet while enabling torque (before any motion) is a bus glitch,
    # not a fault: reconnect up to 3 times. start() disables torque and closes on failure.
    last_error=None
    for attempt in range(3):
        session=HardwareSource(hardware_settings({"camera_mode":"wrist_only"}),teleoperate=False,capture_cameras=False)
        try:
            session.start(explicit_user_start=True,stop=stop)
            last_error=None
            break
        except ConnectionError as exc:
            last_error=exc
            if progress:progress({"state":"start_pose_retry","attempt":attempt+1,"physical_actions":False,"message":"Сбой шины при включении torque, повторное подключение: "+str(exc)})
            time.sleep(1.0)
    if last_error is not None:
        raise RuntimeError("Не удалось включить torque для возврата в стартовую позу после 3 попыток: "+str(last_error))
    try:
        deadline=time.monotonic()+timeout_s
        settled=0
        while True:
            if stop and stop():
                return {"status":"stopped","moved":moved}
            observed=session.observe(require_images=False)
            state=observed["observation.state"]
            error=[t-v for t,v in zip(target,state)]
            limit=[tolerance]*5+[8.0]
            if all(abs(e)<=l for e,l in zip(error,limit)):
                settled+=1
                if settled>=5:
                    break
            else:
                settled=0
            if time.monotonic()>deadline:
                raise RuntimeError("Не удалось плавно вернуть руку в стартовую позу демонстраций за %.0f с; ошибка по суставам: %s"%(timeout_s,[round(e,1) for e in error]))
            if max(abs(e) for e in error[:5])>tolerance and not moved:
                moved=True
                if progress:progress({"state":"moving_to_start_pose","physical_actions":True,"target":target,"start":state,
                                      "message":"Рука возвращается в стартовую позу демонстраций (wrist_roll ≈ %.0f°)"%target[4]})
            session.send(list(target),observed)
            time.sleep(1/30)
    finally:
        errors=session.close()
        if errors:
            raise RuntimeError("Снятие torque после возврата в стартовую позу не подтверждено: "+str(errors))
    return {"status":"ok","moved":moved}


def run_policy(checkpoint: Path, options: dict, progress=None, stop=None):
    """Run the installed LeRobot/LeLab physical rollout, without a second control loop."""
    import os
    import signal
    import subprocess
    import sys
    import uuid
    from .resources import ResourceLease, assert_compute_available
    if not options.get("explicit_user_start"):
        raise PermissionError("Нужен явный запуск политики в новом UI")
    device = options.get("device", "cuda")
    assert_compute_available(device, minimum_available_gb=7.0)
    checkpoint=Path(checkpoint).resolve()
    folder=checkpoint/"pretrained_model" if (checkpoint/"pretrained_model").is_dir() else checkpoint
    manifest_path=folder.parent/"checkpoint.json"
    manifest=json.loads(manifest_path.read_text())
    if manifest.get("status")!="complete" or int(manifest.get("step",0))<1:
        raise ValueError("Checkpoint не завершён")
    if sha256_file(folder/"model.safetensors")!=manifest.get("model_sha256"):
        raise ValueError("Policy weights checksum mismatch")
    kind=json.loads((folder/"config.json").read_text()).get("type")
    if kind not in ("smolvla", "act"):
        raise ValueError("Тип политики не поддерживается штатным LeRobot rollout: "+str(kind))
    task=str(options.get("task","")).strip()
    if kind=="smolvla" and not task:
        raise ValueError("Нужен текст задачи для SmolVLA inference")
    lineage=json.loads((folder/"lineage.json").read_text())
    contract=FeatureContract.from_dict(lineage.get('feature_contract',lineage.get('options',{})))
    if options.get('execution_mode') == 'nearest_successful_demo':
        return _run_nearest_successful_demo(folder, contract, lineage, options, progress, stop)
    if contract.mode=='sam_objects':
        raise ValueError('Этот checkpoint требует live SAM 3 и не может запускаться штатным быстрым LeRobot rollout. Для физического запуска обучите рабочий Wrist RGB + joints профиль')
    if contract.mode == 'wrist_color_object':
        # HardwareSource owns the hardware/camera leases for the full physical
        # session.  Acquire only compute here; double-acquiring those leases
        # would make the worker block itself before the first observation.
        with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"):
            loaded = load_checkpoint(checkpoint, device=device, resource_check=False)
            return _run_policy_loaded(loaded, options, progress, stop)
    fps,episode_steps=_recording_profile(lineage)
    duration=episode_steps/fps
    if options.get("reset_to_demo_start",True):
        start_pose=demo_start_pose(lineage)
        if start_pose is not None:
            result=move_to_start_pose(start_pose,progress,stop)
            if result["status"]=="stopped":
                return {"status":"stopped","backend":"start_pose","steps":0}
            time.sleep(.5)
    command=official_lerobot_command(folder,task,fps,duration,device,policy_type=kind)
    log_path=Path(__file__).resolve().parents[2]/"logs"/("lerobot-rollout-"+uuid.uuid4().hex+".log")
    trace_path=log_path.with_suffix(".trace.jsonl")
    log_path.parent.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",TOKENIZERS_PARALLELISM="false",PYTHONUNBUFFERED="1")
    if kind=="act":env["SO101_ROLLOUT_TRACE"]=str(trace_path)
    if progress:
        progress({"state":"starting_official_lerobot_"+("rtc" if kind=="smolvla" else "sync"),
                  "physical_actions":False,"fps":fps,"duration_s":duration,
                  "message":"Запускается штатный LeRobot rollout из LeLab"})
    stopped=False
    with ResourceLease("gpu_compute" if device.startswith("cuda") else "cpu_compute"),ResourceLease("hardware"),ResourceLease("cameras"):
        with log_path.open("w",buffering=1) as output:
            process=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[2],stdin=subprocess.DEVNULL,
                                     stdout=output,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            while process.poll() is None:
                if stop and stop():
                    stopped=True
                    os.killpg(process.pid,signal.SIGINT)
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
                    break
                time.sleep(.05)
            code=process.wait()
    tail="\n".join(log_path.read_text(errors="replace").splitlines()[-20:])
    if code and not stopped:
        raise RuntimeError("Штатный LeRobot rollout завершился с ошибкой:\n"+tail[-3000:])
    return {"status":"stopped" if stopped else "completed","backend":"official_lerobot_"+("rtc" if kind=="smolvla" else "sync"),
            "fps":fps,"duration_s":duration,"log":str(log_path),
            "trace":str(trace_path) if trace_path.is_file() else None,"exit_code":code}


def _run_nearest_successful_demo(folder, contract, lineage, options, progress, stop):
    """Camera-selected replay of a physically recorded successful wrist episode.

    This is a deterministic fallback for a learned checkpoint that is outside
    its demonstrated distribution. Start pose, nearest example and joint
    offsets come from measured encoders plus the current wrist image; no scene
    coordinate or hand-authored grasp point is used.
    """
    import numpy as np
    from PIL import Image
    from .features import _wrist_color_features, action_state, joint_state, safe_child
    from .hardware import HardwareSource, JOINTS, hardware_settings

    def live_colour_features(image):
        return _wrist_color_features(image,contract.target_color,allow_dominant_nonconvex=True)

    if contract.camera_mode != 'wrist_only_experimental' or contract.target_color not in ('red','yellow','green','blue'):
        raise ValueError('Nearest-demo fallback requires a colour-target wrist-only checkpoint')
    dataset=Path(lineage['dataset_path']).resolve()
    episode_ids=list(lineage.get('split',{}).get('train') or [])
    sam_cache_path=Path(__file__).resolve().parents[2]/'runtime/retrieval-sam-bboxes.json'
    if not sam_cache_path.is_file():raise RuntimeError('Нет разреженного SAM-кэша start/contact')
    sam_cache=json.loads(sam_cache_path.read_text())
    colour_cache_path=Path(__file__).resolve().parents[2]/'runtime/retrieval-colour-trajectories.json'
    if not colour_cache_path.is_file():raise RuntimeError('Нет кэша wrist-локализации успешных траекторий')
    colour_cache=json.loads(colour_cache_path.read_text())
    if Path(colour_cache.get('dataset','')).resolve()!=dataset:
        raise RuntimeError('Кэш wrist-локализации относится к другому датасету')
    colour_episodes=colour_cache.get('episodes',{})
    candidates=[]
    for episode_id in episode_ids:
        episode=safe_child(dataset/'episodes',episode_id)
        metadata=json.loads((episode/'episode.json').read_text())
        if metadata.get('status')=='complete':
            rows=[json.loads(line) for line in (episode/'frames.jsonl').read_text().splitlines() if line.strip()]
            if rows:
                cached=sam_cache.get(episode_id,{})
                visual=cached.get('start',[0.]*6);contact=cached.get('contact',[0.]*6)
                candidate_gripper=np.asarray([action_state(row)[5] for row in rows])
                candidate_close=int(np.argmin(np.diff(candidate_gripper)))+1
                recorded_colours=colour_episodes.get(episode_id)
                if not isinstance(recorded_colours,list) or len(recorded_colours)!=len(rows):
                    raise RuntimeError('Кэш wrist-локализации неполон для '+episode_id)
                recorded_colours=[np.asarray(value,float) for value in recorded_colours]
                start_colour=recorded_colours[0]
                contact_colour=recorded_colours[candidate_close]
                if visual[0] and start_colour[0] and contact_colour[0]:
                    candidates.append({'id':episode_id,'path':episode,'rows':rows,
                        'start':np.asarray(joint_state(rows[0]),float),'visual':np.asarray(visual[1:5],float),
                        'start_colour':start_colour,'contact_colour':contact_colour,'close_at':candidate_close,
                        'recorded_visual':recorded_colours,'contact_visual':np.asarray(contact,float)})
    if len(candidates)<3:raise ValueError('Недостаточно успешных train-эпизодов с видимым целевым кубиком')
    starts=np.stack([item['start'] for item in candidates]);start_scale=starts.std(0).clip(.5,None)
    if progress:progress({'state':'retrieval_loading_sam','physical_actions':False})
    from .annotations import SAM3Segmenter
    segmenter=SAM3Segmenter({'device':'cuda'}).load()
    settings=hardware_settings({'camera_mode':'two_camera'})
    session=HardwareSource(settings,teleoperate=False)
    steps=0;selected=None;last_visual=None
    return_budget=round(np.median([len(item['rows']) for item in candidates]))
    try:
        session.start(explicit_user_start=True,stop=stop)
        if stop and stop():return {'status':'stopped','steps':steps,'checkpoint':str(folder)}
        # Closed-loop wrist servo starts from the actual encoder state.  An
        # unconditional return to the median recording pose moved the camera
        # away from a cube that the current pose already saw clearly.
        observed=session.observe();initial_image=observed['images']['wrist']['image']
        detections=segmenter.segment(initial_image)
        h,w=initial_image.shape[:2]
        def select_target_detection(items,image):
            ih,iw=image.shape[:2]
            colour=np.asarray(live_colour_features(image),float)
            objects=[]
            for detection in items:
                x1,y1,x2,y2=map(float,detection['bbox_xyxy']);area=(x2-x1)*(y2-y1)/(iw*ih)
                if .005<=area<=.4:objects.append(detection)
            if not objects:return None
            if colour[0]:
                cx,cy,bw,bh=colour[1:5]
                colour_box=np.asarray([(cx-bw/2)*iw,(cy-bh/2)*ih,(cx+bw/2)*iw,(cy+bh/2)*ih])
                overlap=[]
                for detection in objects:
                    box=np.asarray(detection['bbox_xyxy'],float)
                    intersection=max(0.,min(box[2],colour_box[2])-max(box[0],colour_box[0]))*max(0.,min(box[3],colour_box[3])-max(box[1],colour_box[1]))
                    if intersection>0:overlap.append((intersection/max(1.,bw*iw*bh*ih),float(detection.get('score',0)),detection))
                if overlap:return max(overlap,key=lambda item:(item[0],item[1]))[2]
            cubes=[detection for detection in objects if detection.get('category')=='cube']
            return max(cubes or objects,key=lambda detection:float(detection.get('score',0)))
        initial_cube=select_target_detection(detections,initial_image)
        if initial_cube is None:raise RuntimeError('SAM3 не нашёл отдельный предмет в wrist overview')
        initial_bbox=initial_cube['bbox_xyxy']
        tracker=_OpticalBBoxTracker(initial_image,initial_bbox)
        x1,y1,x2,y2=map(float,initial_bbox)
        full_initial=np.asarray([1.,(x1+x2)/(2*w),(y1+y2)/(2*h),(x2-x1)/w,(y2-y1)/h,1.])
        colour_initial=np.asarray(live_colour_features(initial_image),float)
        def target_features(image):
            colour=np.asarray(live_colour_features(image),float)
            if colour[0]:
                ih,iw=image.shape[:2];cx,cy,bw,bh=colour[1:5]
                tracker.reset(image,[(cx-bw/2)*iw,(cy-bh/2)*ih,(cx+bw/2)*iw,(cy+bh/2)*ih])
                return colour.tolist()
            return tracker.update(image)
        live_visual=np.asarray(target_features(initial_image))
        if not live_visual[0]:raise RuntimeError('Целевой кубик не найден после возврата в wrist overview')
        live_state=np.asarray(observed['observation.state'],float)
        def visual_alignment_score(recorded,live):
            recorded=np.asarray(recorded,float);live=np.asarray(live,float)
            width=max(recorded[2],1/640);height=max(recorded[3],1/480)
            delta=np.asarray([(recorded[0]-live[0])/width,(recorded[1]-live[1])/height,
                              np.log(max(live[2],1/640)/width),np.log(max(live[3],1/480)/height)])
            return float(np.linalg.norm(delta))
        for item in candidates:
            usable=[index for index,value in enumerate(item['recorded_visual'][:item['close_at']]) if value[0]]
            if not usable:raise RuntimeError('В успешной демонстрации нет видимого кубика до контакта: '+item['id'])
            item['replay_start']=min(usable,key=lambda index:visual_alignment_score(item['recorded_visual'][index][1:5],live_visual[1:5]))
            item['visual_score']=visual_alignment_score(item['recorded_visual'][item['replay_start']][1:5],live_visual[1:5])
            item['joint_score']=float(np.linalg.norm((item['start']-live_state)/start_scale))
            item['score']=item['visual_score']
        selected=min(candidates,key=lambda item:item['visual_score'])
        gripper=np.asarray([action_state(row)[5] for row in selected['rows']]);close_at=selected['close_at']
        open_at=int(np.argmax(np.diff(gripper)))+1
        open_gripper=float(gripper[:close_at].max())
        held_gripper=float(gripper[close_at:open_at].min()) if open_at>close_at else float(gripper.min())
        recorded_visual=selected['recorded_visual']
        # Open-loop approach replay lost the cube when its live placement did
        # not exactly match one demonstration.  Start at the contact phase and
        # let the closed-loop wrist servo below produce the approach from the
        # measured overview pose.  The recorded transfer remains unchanged.
        replay_start=close_at
        replay_reference=selected['contact_colour']
        replay_error=np.abs(replay_reference[1:5]-live_visual[1:5])
        current_visual=live_visual
        live_state=np.asarray(observed['observation.state'],float)
        replay_state=np.asarray(joint_state(selected['rows'][replay_start]),float)
        offset=live_state-replay_state;offset[5]=0.
        selected['exact_live_visual']=current_visual
        selected['exact_visual_score']=visual_alignment_score(replay_reference[1:5],current_visual[1:5])
        jacobian_inputs=[];jacobian_outputs=[]
        previous_sample=None
        for local_index in range(max(0,close_at-100),close_at):
            visual=np.asarray(recorded_visual[local_index],float)
            sample=(np.asarray(joint_state(selected['rows'][local_index])[:5],float),visual[1:5]) if visual[0] else None
            if previous_sample is not None and sample is not None:
                jacobian_inputs.append(sample[0]-previous_sample[0])
                jacobian_outputs.append(sample[1]-previous_sample[1])
            previous_sample=sample
        if len(jacobian_inputs)<10:raise RuntimeError('Недостаточно wrist-наблюдений для контактного visual servo')
        image_jacobian=np.linalg.lstsq(np.asarray(jacobian_inputs),np.asarray(jacobian_outputs),rcond=None)[0].T
        image_jacobian_pinv=np.linalg.pinv(image_jacobian)
        approach_commands=[]
        for item in candidates:
            approach_commands.extend(np.asarray(action_state(row)[:5],float) for row in item['rows'][:item['close_at']])
        approach_commands=np.asarray(approach_commands)
        contact_low=approach_commands.min(0);contact_high=approach_commands.max(0)
        contact_alignment_budget=int(np.ceil(float(np.max(contact_high-contact_low))/session.cfg['max_step_degrees']))
        valid_contacts=np.asarray([item['contact_visual'] for item in candidates if item['contact_visual'][0]])
        if not len(valid_contacts):raise RuntimeError('В SAM-кэше нет контактных масок кубика')
        contact_colours=np.stack([item['contact_colour'][1:5] for item in candidates])
        visual_resolution=np.asarray([1/w,1/h,1/w,1/h])
        contact_colour_low=contact_colours.min(0)-visual_resolution
        contact_colour_high=contact_colours.max(0)+visual_resolution
        contact_reference=np.asarray([1.,*np.median(contact_colours,axis=0),1.])
        sam_contact_low=valid_contacts[:,1:5].min(0)
        sam_contact_high=valid_contacts[:,1:5].max(0)
        if progress:progress({'state':'retrieval_selected','episode':selected['id'],'score':selected['exact_visual_score'],
            'joint_score':selected['joint_score'],
            'live_visual':selected['exact_live_visual'].tolist(),'recorded_visual':replay_reference.tolist(),
            'replay_start':replay_start,'replay_error':replay_error.tolist(),
            'joint_offset':offset.tolist(),'physical_actions':False})
        period=(selected['rows'][1]['timestamp_ns']-selected['rows'][0]['timestamp_ns'])/1e9 if len(selected['rows'])>1 else 1/30
        # Reproduce the coordinated command trajectory at its recorded cadence.
        # Pausing one waypoint until every servo matches it forces individual
        # joints against a pose which never existed in the original motion.
        last_visible_visual=selected['exact_live_visual'].copy();last_visible_frame=-1
        contact_servo_offset=np.zeros(5,float)
        diagnostic_dir=Path(__file__).resolve().parents[2]/'runtime/retrieval-contact'
        diagnostic_dir.mkdir(parents=True,exist_ok=True)
        def restore_open_pose(target):
            nonlocal steps
            if target is None:return False
            for restore_cycle in range(return_budget):
                if stop and stop():return False
                restore_observed=session.observe()
                restore_state=np.asarray(restore_observed['observation.state'],float)
                restore_error=np.abs(np.asarray(target,float)-restore_state)
                if float(restore_error[:5].max())<=session.cfg['max_step_degrees']:return True
                restore_target=np.asarray(target,float).copy();restore_target[5]=open_gripper
                session.send(validate_policy_target(restore_target,session.calibration),restore_observed);steps+=1
                time.sleep(period)
            return False
        for frame_index in range(replay_start,len(selected['rows'])):
            row=selected['rows'][frame_index]
            if stop and stop():break
            cycle=time.monotonic()
            desired_state=np.asarray(joint_state(row),float)+offset
            desired_state[5]=joint_state(row)[5]
            # Preserve the demonstrated motor lead (the leader target versus
            # measured follower state).  It is the feed-forward effort that
            # overcame gravity/friction in the successful recording.  Encoder
            # feedback below still decides when the path may advance.
            command=np.asarray(action_state(row),float)+offset
            command[5]=action_state(row)[5]
            if frame_index<close_at:command[5]=open_gripper
            if frame_index==close_at:
                recorded_contact=np.asarray(contact_reference,float)
                aligned=False;verified_contact=None
                best_score=float('inf');best_state=None;non_improving=0
                previous_servo_state=None;previous_servo_visual=None
                # Measure a local image Jacobian at the live overview pose.
                # The demonstration-wide least-squares Jacobian is only an
                # initial prior and was wrong for a cube at the image edge.
                measured_jacobian=image_jacobian.copy();measured_columns=0
                for joint_index in range(5):
                    if stop and stop():break
                    probe_origin=session.observe()
                    origin_state=np.asarray(probe_origin['observation.state'],float)
                    origin_visual=np.asarray(live_colour_features(probe_origin['images']['wrist']['image']),float)
                    positive_room=contact_high[joint_index]-origin_state[joint_index]
                    negative_room=origin_state[joint_index]-contact_low[joint_index]
                    origin_target=np.clip(origin_visual[1:5],contact_colour_low,contact_colour_high) if origin_visual[0] else origin_visual[1:5]
                    origin_error=origin_target-origin_visual[1:5]
                    predicted_improvement=float(image_jacobian[:,joint_index]@origin_error)
                    direction=1. if predicted_improvement>=0 else -1.
                    if direction>0 and positive_room<=0:direction=-1.
                    if direction<0 and negative_room<=0:direction=1.
                    room=positive_room if direction>0 else negative_room
                    probe_distance=min(session.cfg['max_step_degrees'],max(0.,room))
                    probe_observed=probe_origin;probe_state=origin_state.copy();probe_visual=origin_visual.copy()
                    if origin_visual[0] and probe_distance>0:
                        probe_target=origin_state.copy();probe_target[joint_index]+=direction*probe_distance
                        for probe_cycle in range(len(JOINTS)):
                            if stop and stop():break
                            session.send(validate_policy_target(probe_target,session.calibration),probe_observed);steps+=1
                            time.sleep(period)
                            probe_observed=session.observe();probe_state=np.asarray(probe_observed['observation.state'],float)
                            probe_visual=np.asarray(live_colour_features(probe_observed['images']['wrist']['image']),float)
                            if abs(probe_state[joint_index]-origin_state[joint_index])>=probe_distance/2:break
                        actual_delta=probe_state[joint_index]-origin_state[joint_index]
                        if probe_visual[0] and abs(actual_delta)>.05:
                            measured_column=(probe_visual[1:5]-origin_visual[1:5])/actual_delta
                            resolved=bool(np.any(np.abs(probe_visual[1:5]-origin_visual[1:5])>=visual_resolution))
                            if resolved:
                                measured_jacobian[:,joint_index]=measured_column
                                measured_columns+=1
                        for rollback_cycle in range(return_budget):
                            if stop and stop():break
                            rollback_observed=session.observe()
                            rollback_error=np.abs(origin_state-np.asarray(rollback_observed['observation.state'],float))
                            if float(rollback_error[:5].max())<=360/4095:break
                            session.send(validate_policy_target(origin_state,session.calibration),rollback_observed);steps+=1
                            time.sleep(period)
                if measured_columns:
                    image_jacobian=measured_jacobian
                    image_jacobian_pinv=np.linalg.pinv(image_jacobian)
                if progress:progress({'state':'retrieval_local_jacobian','steps':steps,
                    'measured_columns':measured_columns,'pan_gain':float(image_jacobian[0,0]),
                    'physical_actions':True})
                # A cube clipped by the left/right image edge first needs a
                # well-conditioned horizontal centering move.  Use the locally
                # measured shoulder-pan derivative; no scene-specific angle is
                # stored or guessed.
                pan_gain=float(image_jacobian[0,0])
                pan_gain_samples=[pan_gain]
                pan_budget=return_budget
                pan_best=float('inf');pan_stall=0
                if abs(pan_gain)>=visual_resolution[0]/session.cfg['max_step_degrees']:
                    for pan_cycle in range(pan_budget):
                        if stop and stop():break
                        pan_observed=session.observe()
                        pan_state=np.asarray(pan_observed['observation.state'],float)
                        pan_visual=np.asarray(live_colour_features(pan_observed['images']['wrist']['image']),float)
                        if not pan_visual[0]:
                            time.sleep(period);pan_observed=session.observe()
                            pan_state=np.asarray(pan_observed['observation.state'],float)
                            pan_visual=np.asarray(live_colour_features(pan_observed['images']['wrist']['image']),float)
                        if not pan_visual[0]:raise RuntimeError('Целевая грань потеряна во время горизонтального wrist-наведения')
                        pan_target_x=float(np.clip(pan_visual[1],contact_colour_low[0],contact_colour_high[0]))
                        pan_error=pan_target_x-pan_visual[1]
                        if pan_error==0:break
                        if abs(pan_error)<pan_best:
                            pan_best=abs(pan_error);pan_stall=0
                        else:pan_stall+=1
                        if pan_stall>=len(JOINTS):break
                        pan_delta=float(np.clip(pan_error/pan_gain,-session.cfg['max_step_degrees'],session.cfg['max_step_degrees']))
                        pan_command=pan_state.copy();pan_command[0]=np.clip(pan_state[0]+pan_delta,contact_low[0],contact_high[0]);pan_command[5]=open_gripper
                        if abs(pan_command[0]-pan_state[0])<=360/4095:break
                        sent=session.send(validate_policy_target(pan_command,session.calibration),pan_observed);steps+=1
                        time.sleep(period)
                        pan_after=session.observe();pan_after_state=np.asarray(pan_after['observation.state'],float)
                        pan_after_visual=np.asarray(live_colour_features(pan_after['images']['wrist']['image']),float)
                        actual_pan_delta=pan_after_state[0]-pan_state[0]
                        if pan_after_visual[0] and abs(actual_pan_delta)>.05:
                            measured_pan_gain=float((pan_after_visual[1]-pan_visual[1])/actual_pan_delta)
                            if abs(measured_pan_gain)>=visual_resolution[0]/session.cfg['max_step_degrees'] and measured_pan_gain*pan_gain_samples[0]>0:
                                pan_gain_samples.append(measured_pan_gain)
                                pan_gain=float(np.median(pan_gain_samples))
                        if progress:progress({'state':'retrieval_coarse_center','steps':steps,'cycle':pan_cycle,
                            'target_visual':pan_after_visual.tolist(),'pan_gain':pan_gain,
                            'action':sent['action'],'physical_actions':True})
                horizontal_centered=False;horizontal_best=float('inf');horizontal_stall=0
                horizontal_best_state=None
                failed_horizontal=set();last_horizontal_joint=None
                for horizontal_cycle in range(return_budget):
                    if stop and stop():break
                    horizontal_observed=session.observe()
                    horizontal_state=np.asarray(horizontal_observed['observation.state'],float)
                    horizontal_visual=np.asarray(live_colour_features(horizontal_observed['images']['wrist']['image']),float)
                    if not horizontal_visual[0]:
                        time.sleep(period)
                        horizontal_observed=session.observe()
                        horizontal_state=np.asarray(horizontal_observed['observation.state'],float)
                        horizontal_visual=np.asarray(live_colour_features(horizontal_observed['images']['wrist']['image']),float)
                    if not horizontal_visual[0]:
                        restored=restore_open_pose(horizontal_best_state)
                        if last_horizontal_joint is not None:
                            failed_horizontal.add(last_horizontal_joint);image_jacobian[0,last_horizontal_joint]=0.
                            image_jacobian_pinv=np.linalg.pinv(image_jacobian)
                        if not restored:raise RuntimeError('Целевая грань потеряна, возврат в лучшую coarse-позу не подтверждён')
                        horizontal_observed=session.observe()
                        horizontal_state=np.asarray(horizontal_observed['observation.state'],float)
                        horizontal_visual=np.asarray(live_colour_features(horizontal_observed['images']['wrist']['image']),float)
                        if not horizontal_visual[0]:raise RuntimeError('Целевая грань не восстановилась в лучшей coarse-позе')
                        horizontal_best=float('inf');horizontal_stall=0;last_horizontal_joint=None
                    horizontal_target=float(np.clip(horizontal_visual[1],contact_colour_low[0],contact_colour_high[0]))
                    horizontal_error=horizontal_target-horizontal_visual[1]
                    if horizontal_error==0:
                        horizontal_centered=True
                        break
                    if abs(horizontal_error)<horizontal_best:
                        horizontal_best=abs(horizontal_error);horizontal_best_state=horizontal_state.copy();horizontal_stall=0
                    else:horizontal_stall+=1
                    if horizontal_stall>=len(JOINTS) and last_horizontal_joint is not None:
                        failed_horizontal.add(last_horizontal_joint)
                        image_jacobian[0,last_horizontal_joint]=0.
                        image_jacobian_pinv=np.linalg.pinv(image_jacobian)
                        horizontal_best=abs(horizontal_error);horizontal_stall=0
                    choices=[]
                    for joint_index in range(5):
                        gain=float(image_jacobian[0,joint_index])
                        if joint_index not in failed_horizontal and abs(gain)>=visual_resolution[0]/session.cfg['max_step_degrees']:
                            requested_delta=float(np.clip(horizontal_error/gain,-session.cfg['max_step_degrees'],session.cfg['max_step_degrees']))
                            bounded_target=float(np.clip(horizontal_state[joint_index]+requested_delta,contact_low[joint_index],contact_high[joint_index]))
                            actual_request=bounded_target-horizontal_state[joint_index]
                            predicted=gain*actual_request
                            if abs(actual_request)>360/4095 and predicted*horizontal_error>0:
                                choices.append((abs(predicted),joint_index,bounded_target,gain))
                    if not choices:break
                    _,joint_index,bounded_target,old_gain=max(choices,key=lambda item:item[0])
                    last_horizontal_joint=joint_index
                    horizontal_command=horizontal_state.copy();horizontal_command[joint_index]=bounded_target;horizontal_command[5]=open_gripper
                    sent=session.send(validate_policy_target(horizontal_command,session.calibration),horizontal_observed);steps+=1
                    time.sleep(period)
                    horizontal_after=session.observe();after_state=np.asarray(horizontal_after['observation.state'],float)
                    after_visual=np.asarray(live_colour_features(horizontal_after['images']['wrist']['image']),float)
                    actual_delta=after_state[joint_index]-horizontal_state[joint_index]
                    if after_visual[0] and abs(actual_delta)>.05:
                        measured_gain=float((after_visual[1]-horizontal_visual[1])/actual_delta)
                        if abs(measured_gain)>=visual_resolution[0]/session.cfg['max_step_degrees'] and measured_gain*old_gain>0:
                            image_jacobian[0,joint_index]=measured_gain
                            image_jacobian_pinv=np.linalg.pinv(image_jacobian)
                    if progress:progress({'state':'retrieval_coarse_center_joint','steps':steps,'cycle':horizontal_cycle,
                        'joint':JOINTS[joint_index],'target_visual':after_visual.tolist(),
                        'action':sent['action'],'physical_actions':True})
                if not horizontal_centered:
                    raise RuntimeError('Горизонтальное wrist-наведение исчерпало измеренный диапазон успешных подходов')
                for alignment_cycle in range(contact_alignment_budget):
                    if stop and stop():break
                    alignment_started=time.monotonic();observed=session.observe()
                    contact_visual=np.asarray(live_colour_features(observed['images']['wrist']['image']),float)
                    if not contact_visual[0]:raise RuntimeError('Целевая грань потеряна во время замкнутого wrist-подхода')
                    last_visible_visual=contact_visual.copy();last_visible_frame=frame_index
                    servo_visual=contact_visual
                    current_servo_state=np.asarray(observed['observation.state'],float)
                    moved=False
                    if previous_servo_state is not None:
                        joint_delta=current_servo_state[:5]-previous_servo_state[:5]
                        if float(np.linalg.norm(joint_delta))>.05:
                            visual_delta=servo_visual[1:5]-previous_servo_visual
                            residual=visual_delta-image_jacobian@joint_delta
                            image_jacobian+=np.outer(residual,joint_delta)/float(joint_delta@joint_delta)
                            image_jacobian_pinv=np.linalg.pinv(image_jacobian)
                            moved=True
                    previous_servo_state=current_servo_state.copy();previous_servo_visual=servo_visual[1:5].copy()
                    nearest_successful=np.clip(servo_visual[1:5],contact_colour_low,contact_colour_high)
                    pixel_error=nearest_successful-servo_visual[1:5]
                    aligned=bool(np.all(pixel_error==0))
                    score=float(np.linalg.norm(pixel_error/np.maximum(contact_colour_high-contact_colour_low,visual_resolution)))
                    if score<best_score:
                        best_score=score;best_state=current_servo_state.copy();non_improving=0
                    elif moved:non_improving+=1
                    if aligned:
                        # The controller and its Jacobian use the same blue-face
                        # feature learned from demonstrations.  SAM independently
                        # verifies that this face belongs to a full object with
                        # geometry observed at successful contact before closure.
                        reacquired=segmenter.segment(observed['images']['wrist']['image'])
                        detection=select_target_detection(reacquired,observed['images']['wrist']['image'])
                        if detection is None:raise RuntimeError('SAM3 не подтвердил полный кубик в контактной области')
                        ih,iw=observed['images']['wrist']['image'].shape[:2]
                        x1,y1,x2,y2=map(float,detection['bbox_xyxy'])
                        verified_contact=np.asarray([(x1+x2)/(2*iw),(y1+y2)/(2*ih),(x2-x1)/iw,(y2-y1)/ih])
                        if not bool(np.all(verified_contact>=sam_contact_low) and np.all(verified_contact<=sam_contact_high)):
                            raise RuntimeError('SAM3 видит кубик, но его полный размер/положение не соответствует успешному контакту')
                        # SAM can exceed the state watchdog; refresh encoders
                        # after the read-only verification and before any send.
                        observed=session.observe()
                        aligned_state=np.asarray(observed['observation.state'],float)
                        offset=aligned_state-np.asarray(joint_state(row),float);offset[5]=0.
                        desired_state=np.asarray(joint_state(row),float)+offset
                        desired_state[5]=joint_state(row)[5]
                        command=np.asarray(action_state(row),float)+offset
                        command[5]=action_state(row)[5]
                        contact_servo_offset=np.zeros(5,float)
                        break
                    if non_improving>=len(contact_servo_offset):
                        if best_state is not None:
                            for rollback_cycle in range(contact_alignment_budget):
                                rollback_observed=session.observe()
                                rollback_error=np.abs(best_state-np.asarray(rollback_observed['observation.state'],float))
                                if float(rollback_error[:5].max())<=session.cfg['max_step_degrees']:break
                                session.send(validate_policy_target(best_state,session.calibration),rollback_observed)
                        raise RuntimeError('Контактное наведение начало расходиться; восстановлено лучшее измеренное положение, захват не закрыт')
                    correction=image_jacobian_pinv@pixel_error
                    correction=np.clip(correction,-session.cfg['max_step_degrees'],session.cfg['max_step_degrees'])
                    alignment_command=current_servo_state.copy()
                    alignment_command[:5]=np.clip(current_servo_state[:5]+correction,contact_low,contact_high)
                    alignment_command[5]=open_gripper
                    sent=session.send(validate_policy_target(alignment_command,session.calibration),observed);steps+=1
                    if progress:progress({'state':'retrieval_contact_alignment','steps':steps,
                        'frame':frame_index,'alignment_cycle':alignment_cycle,
                        'target_visual':contact_visual.tolist(),'recorded_visual':recorded_contact.tolist(),
                        'action':sent['action'],'physical_actions':True})
                    remaining=alignment_started+period-time.monotonic()
                    if remaining>0:time.sleep(remaining)
                if stop and stop():break
                if not aligned:raise RuntimeError('Кубик не совместился с записанной контактной областью; захват не закрыт')
                Image.fromarray(np.asarray(observed['images']['wrist']['image'])).save(diagnostic_dir/'before-close-wrist.png')
                Image.fromarray(np.asarray(observed['images']['top']['image'])).save(diagnostic_dir/'before-close-top.png')
                (diagnostic_dir/'before-close.json').write_text(json.dumps({
                    'episode':selected['id'],'frame':frame_index,'live_visual':servo_visual.tolist(),
                    'recorded_contact':recorded_contact.tolist(),'servo_offset':contact_servo_offset.tolist(),
                    'sam_contact':verified_contact.tolist() if verified_contact is not None else None,
                    'state':[float(value) for value in observed['observation.state']],
                    'command':[float(value) for value in command]},indent=2))
            if frame_index>=close_at:
                command[:5]+=contact_servo_offset
            if frame_index==open_at:
                # Do not release while the arm is still in transit.  Hold the
                # recorded coordinated transfer target briefly with the object
                # grasped, then allow the demonstrated opening sequence.
                for settle_cycle in range(30):
                    if stop and stop():break
                    settle_started=time.monotonic();observed=session.observe()
                    measured=np.asarray(observed['observation.state'],float)
                    error=np.abs(desired_state-measured)
                    demonstrated_lag=np.abs(command-desired_state)
                    allowed_lag=demonstrated_lag+np.asarray(
                        [session.cfg['max_step_degrees']]*5+[session.cfg['max_gripper_step']])
                    if bool(np.all(error[:5]<=allowed_lag[:5])):break
                    settle_command=command.copy();settle_command[5]=held_gripper
                    sent=session.send(validate_policy_target(settle_command,session.calibration),observed,
                                      allowed_measured_lead=allowed_lag);steps+=1
                    remaining=settle_started+period-time.monotonic()
                    if remaining>0:time.sleep(remaining)
            observed=session.observe()
            last_visual=target_features(observed['images']['wrist']['image'])
            if last_visual[0]:
                last_visible_visual=np.asarray(last_visual,float);last_visible_frame=frame_index
            if frame_index<=close_at+20 and not last_visual[0]:
                occluded_at_contact=(frame_index>=close_at-5 and last_visible_frame>=close_at-5)
                if not occluded_at_contact:
                    raise RuntimeError(f'Целевой кубик потерян до подтверждённой фазы захвата: frame {frame_index}/{close_at}')
            measured=np.asarray(observed['observation.state'],float)
            error=np.abs(desired_state-measured)
            demonstrated_lag=np.abs(command-desired_state)
            allowed_lag=demonstrated_lag+np.asarray(
                [session.cfg['max_step_degrees']]*5+[session.cfg['max_gripper_step']])
            sent=session.send(validate_policy_target(command,session.calibration),observed,
                              allowed_measured_lead=allowed_lag);steps+=1
            if frame_index==close_at+20:
                Image.fromarray(np.asarray(observed['images']['wrist']['image'])).save(diagnostic_dir/'after-close-wrist.png')
                Image.fromarray(np.asarray(observed['images']['top']['image'])).save(diagnostic_dir/'after-close-top.png')
                (diagnostic_dir/'after-close.json').write_text(json.dumps({
                    'episode':selected['id'],'frame':frame_index,'live_visual':last_visual,
                    'state':[float(value) for value in observed['observation.state']],
                    'command':[float(value) for value in command],
                    'recorded_state':[float(value) for value in joint_state(row)]},indent=2))
            if progress:progress({'state':'retrieval_replay','steps':steps,'frame':frame_index,
                'frames':len(selected['rows']),'close_frame':close_at,'episode':selected['id'],
                'target_visual':last_visual,'recorded_visual':recorded_visual[frame_index],
                'tracking_error':{'joint':float(error[:5].max()),'gripper':float(error[5])},
                'action':sent['action'],'physical_actions':True})
            remaining=cycle+period-time.monotonic()
            if remaining>0:time.sleep(remaining)
    finally:
        stop_errors=session.close()
        if stop_errors:raise RuntimeError('Снятие torque не подтверждено: '+str(stop_errors))
    return {'status':'stopped' if stop and stop() else 'completed','backend':'nearest_successful_demo',
            'steps':steps,'episode':selected['id'] if selected else None,'score':selected['score'] if selected else None,
            'last_target_visual':last_visual,'checkpoint':str(folder)}


def official_lerobot_command(folder: Path,task: str,fps: float,duration: float,device="cuda",policy_type="smolvla"):
    """Build the installed LeRobot rollout command for the saved policy type."""
    import json
    import sys
    from .hardware import DEFAULT_CAMERAS
    fps_value=int(fps)
    if fps_value!=fps:
        raise ValueError("LeRobot rollout требует целую частоту кадров")
    cameras={"wrist":{"type":"opencv","index_or_path":DEFAULT_CAMERAS["wrist"],"width":640,
                       "height":480,"fps":fps_value,"fourcc":"YUYV"}}
    command=[sys.executable,"-m","lerobot.scripts.lerobot_rollout","--strategy.type=base",
            f"--policy.path={Path(folder).resolve()}",f"--policy.device={device}",
            "--robot.type=so101_follower","--robot.port=/dev/so101-follower","--robot.id=so101_follower",
            "--return_to_initial_position=true",f"--device={device}",f"--task={task}",
            f"--duration={duration}",f"--fps={fps_value}",
            "--robot.cameras="+json.dumps(cameras,separators=(",",":"))]
    if policy_type=="smolvla":
        command.extend(["--inference.type=rtc","--inference.rtc.execution_horizon=10",
                        "--inference.rtc.max_guidance_weight=10.0"])
    elif policy_type=="act":
        command.extend(["--discover_packages_path=so101_lab.lerobot_plugin","--inference.type=sync"])
    else:
        raise ValueError("Неизвестный тип политики: "+str(policy_type))
    return command


def _run_policy_loaded(loaded, options, progress, stop):
    import hashlib
    import numpy as np
    from .hardware import HardwareSource, JOINTS, hardware_settings
    if not options.get("explicit_user_start"):
        raise PermissionError("Нужен явный запуск политики в новом UI")
    segmenter = None
    associators = {}
    if loaded.contract.mode == "sam_objects":
        segmenter = _load_segmenter(loaded)  # before bus/torque
    hardware_options=dict(options.get("hardware", {}))
    hardware_options["camera_mode"]="wrist_only" if loaded.contract.cameras==("wrist",) else "two_camera"
    settings = hardware_settings(hardware_options)
    steps = 0
    max_steps = int(options.get("max_steps",getattr(loaded,"episode_steps",300)))
    control_hz=float(getattr(loaded,"control_hz",settings["fps"]))
    if not 1<=control_hz<=120:
        raise ValueError("Некорректная частота исполнения политики")
    action_period=1/control_hz
    task = str(options.get("task", ""))
    if not task.strip() and loaded.policy.name=="smolvla":
        raise ValueError("Нужен текст задачи для SmolVLA inference")
    if stop and stop():return {"status":"stopped","steps":0,"checkpoint":str(loaded.checkpoint)}
    policy_config = getattr(loaded.policy, "config", None)
    ensemble_coeff = getattr(policy_config, "temporal_ensemble_coeff", None)
    act_action_steps = getattr(policy_config, "n_action_steps", None)
    if loaded.contract.mode == "wrist_color_object":
        # CUDA/cuDNN initialization takes hundreds of milliseconds in a fresh
        # worker. Warm it with a schema-valid inert observation before opening
        # the camera, bus or torque; the real first frame must still satisfy the
        # unchanged 150 ms freshness gate.
        stamp=time.monotonic_ns()
        warm_image=np.zeros((480,640,3),dtype=np.uint8)
        warm_frame={"frame_id":"policy_warmup","timestamp_ns":stamp,"state_timestamp_ns":stamp,
                    "observation.state":[0.0]*6,"state_valid":True,
                    "cameras":{"wrist":{"timestamp_ns":stamp,"valid":True,"sha256":"warmup"}}}
        loaded.predict(warm_frame,{"wrist":warm_image},None,task,reset_queue=True)
        loaded.reset_actions()
        if stop and stop():return {"status":"stopped","steps":0,"checkpoint":str(loaded.checkpoint)}
    session = HardwareSource(settings, teleoperate=False)
    stale_replans = 0
    try:
        session.start(explicit_user_start=True,stop=stop)
        while steps < max_steps and not (stop and stop()):
            cycle_started = time.monotonic()
            observed = session.observe()
            images = {cam: item["image"] for cam, item in observed["images"].items()}
            frame = {k: v for k, v in observed.items() if k != "images"}
            frame["frame_id"] = f"live_{steps:08d}"
            frame["cameras"] = {}
            for cam, item in observed["images"].items():
                # Live annotations (when used) only need a stable identity for
                # this exact in-memory frame. Encoding 640x480 PNG here added
                # ~135 ms before every prediction and made a 30 ms ACT action
                # fail the freshness watchdog. Archived datasets keep their
                # independently verified PNG hashes unchanged.
                raw_hash=hashlib.sha256(item["image"].tobytes(order="C")).hexdigest()
                frame["cameras"][cam] = {"timestamp_ns": item["timestamp_ns"], "valid": True,
                    "sha256": raw_hash, "path": None}
            annotation = segmenter.annotate_observation(frame, images, associators) if segmenter else None
            action = loaded.predict(frame, images, annotation, task)
            elapsed = (time.monotonic_ns()-frame["timestamp_ns"])/1e6
            age_limit = min(float(options.get("max_observation_age_ms", MAX_OBSERVATION_AGE_MS)),
                            150.0 if loaded.contract.mode == "wrist_color_object" else MAX_OBSERVATION_AGE_MS)
            revalidated_initial = None
            if elapsed > age_limit:
                if steps == 0 and loaded.contract.mode == "wrist_color_object":
                    # Torque only holds the measured start pose before command
                    # zero. Revalidate both encoders and target geometry on a
                    # newly captured frame instead of widening the age limit.
                    from .features import _wrist_color_features
                    latest=session.observe()
                    old_target=_wrist_color_features(images["wrist"],loaded.contract.target_color)
                    new_target=_wrist_color_features(latest["images"]["wrist"]["image"],loaded.contract.target_color)
                    state_change=max(abs(a-b) for a,b in zip(latest["observation.state"],frame["observation.state"]))
                    pixel_change=max(abs(a-b) for a,b in zip(old_target[1:5],new_target[1:5])) if old_target[0] and new_target[0] else float("inf")
                    visual_tolerance=max(2/640, .05*min(old_target[3:5])) if old_target[0] else 0.
                    if state_change <= .75 and pixel_change <= visual_tolerance:
                        revalidated_initial=latest
                        if progress:
                            progress({"state":"initial_observation_revalidated","steps":0,
                                      "observation_age_ms":elapsed,"state_change":state_change,
                                      "target_change":pixel_change,"target_tolerance":visual_tolerance,
                                      "physical_actions":False,
                                      "message":"Первое предсказание повторно подтверждено свежими энкодерами и wrist-кадром"})
                    elif stale_replans < 2:
                        stale_replans += 1
                        loaded.reset_actions()
                        if progress:
                            progress({"state":"reobserving","steps":0,"observation_age_ms":elapsed,
                                      "state_change":state_change,"target_change":pixel_change,
                                      "stale_replans":stale_replans,"physical_actions":False,
                                      "message":"Начальная сцена изменилась; команда отброшена, берётся новый кадр"})
                        continue
                    else:
                        raise RuntimeError("Первое предсказание устарело, а свежая сцена/энкодеры изменились; действие не отправлено")
                else:
                    raise RuntimeError(f"Policy/SAM observation устарело на {elapsed:.0f} ms; действие не отправлено")
            if stop and stop():
                break
            # Fast wrist-object ACT is trained and executed as one causal
            # observation -> action transaction. A second encoder read after
            # inference used to reject normal motion from the previous target
            # and halve the effective control rate. Slow SAM/VLA still gets the
            # conservative re-observation gate below.
            fresh = revalidated_initial or (frame if loaded.contract.mode == "wrist_color_object" else session.observe())
            state_delta=max(abs(a-b) for a,b in zip(fresh["observation.state"], frame["observation.state"]))
            if loaded.contract.mode != "wrist_color_object" and state_delta > .5:
                # The just-predicted chunk belongs to an observation that no
                # longer matches the arm.  Discard only this stale chunk.
                if hasattr(loaded,"reset_actions"):
                    loaded.reset_actions()
                stale_replans += 1
                if progress:
                    progress({"state":"reobserving","steps":steps,"observation_age_ms":elapsed,
                              "state_delta":state_delta,"stale_replans":stale_replans,
                              "message":"Рука изменила позу во время inference; действие отброшено, снимается новое наблюдение",
                              "physical_actions":False})
                if stale_replans >= 3:
                    raise RuntimeError(f"Robot state remained unstable during {stale_replans} fresh replans; action not sent")
            else:
                stale_replans = 0
                requested_action=[float(value) for value in action]
                action = validate_policy_target(action,session.calibration)
                clipped_joints=[name for name,before,after in zip(JOINTS,requested_action,action) if abs(before-after)>1e-6]
                sent = session.send(action, fresh)  # one send, no automatic retry
                steps += 1
                if progress:
                    target_visual = None
                    if loaded.contract.mode == "wrist_color_object":
                        from .features import _wrist_color_features
                        target_visual = _wrist_color_features(images["wrist"], loaded.contract.target_color)
                    progress({"state": "inference", "steps": steps, "observation_age_ms": elapsed,
                              "action": sent["action"],"policy_action_clipped":clipped_joints,
                              "target_visual": target_visual,
                              "control_hz":control_hz,
                              "execution_mode":"learned",
                              "act_temporal_ensemble":ensemble_coeff if loaded.policy.name == "act" else None,
                              "act_action_steps":act_action_steps,
                              "policy_checkpoint": str(loaded.checkpoint), "physical_actions": True})
                deadline=cycle_started+action_period
                while not (stop and stop()):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        break
                    time.sleep(min(.01,remaining))
    finally:
        stop_errors=session.close()
        if stop_errors:raise RuntimeError("Снятие torque не подтверждено: проверьте аппаратный STOP. "+str(stop_errors))
    return {"status": "stopped" if stop and stop() else "completed", "steps": steps, "checkpoint": str(loaded.checkpoint)}


def validate_policy_target(action,calibration):
    """Saturate finite learned targets to measured calibration limits before servo execution.

    A continuous regression policy is not mathematically range-bounded. Saturation is therefore
    part of converting its output into an actuator target; HardwareSource subsequently applies
    the existing state-relative speed and per-command step limits.
    """
    import math
    from .hardware import JOINTS
    if len(action)!=6 or not all(math.isfinite(float(x)) for x in action):raise ValueError('Политика вернула невалидные суставные команды')
    limits=calibration['values']['follower']
    bounded=[]
    for i,name in enumerate(JOINTS):
        if name=='gripper':low,high=0.,100.
        else:
            cal=limits[name];mid=(cal['range_min']+cal['range_max'])/2
            low=max(-175.,(cal['range_min']-mid)*360/4095);high=min(175.,(cal['range_max']-mid)*360/4095)
        value=float(action[i])
        bounded.append(max(low,min(high,value)))
    return bounded
