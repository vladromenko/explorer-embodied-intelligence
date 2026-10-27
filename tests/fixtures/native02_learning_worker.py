"""Isolated SOFTWARE qualification worker; no fleet config or robot transport.

Optional test instrumentation requests the ORIGINAL durable pause at finished
step4. The same actual optimizer save/RNG/sampler protocol then exits75.
"""
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'shared'),str(root/'00-control-center/src')]

from explorer.store import Store
from explorer_shared.learning.rosmaster_skill_pipeline import train_rosmaster_skill
from explorer_shared.learning import checkpoint_control as checkpoint

store=Store(sys.argv[1]);identity=sys.argv[2];pause=sys.argv[3]=='pause4'
checkpoint.atomic_json(store.root/'jobs'/identity/('qualification-worker-'+sys.argv[3]+'.json'),checkpoint.worker_identity())
if pause:
    original=checkpoint.OptimizerCheckpointControl.maybe_pause
    def request_finished_step4(self,completed,save,validate_source):
        if completed==4:
            checkpoint.request_pause(self.folder,checkpoint.read_json(self.folder/'optimizer-active.json'),'a'*32)
        return original(self,completed,save,validate_source)
    checkpoint.OptimizerCheckpointControl.maybe_pause=request_finished_step4
result=train_rosmaster_skill(store,store.job(identity));store.complete_job(identity,result)
print(json.dumps({'job_id':identity,'trained':result['trained'],'optimizer_steps':result['optimizer_steps'],
    'training_resume':result['training_resume'],'physical_io':result['physical_io']}),flush=True)
