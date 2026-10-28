"""Generated original04 producers with self-contained calibration, never hardware.

Reuse the approved04 writer/import/review/aggregate factory. The configured-file
export is captured BEFORE original writers start; no existing archive is patched.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from explorer_shared.so101_calibration_artifact import configured_calibration_artifact
from test_so101_capture_adapter import FakeSource
from test_transfer_mapping04 import CALIBRATION
import test_so101_approved_capture_cohort as original


def geometry_source_cohort(tmp_path,monkeypatch):
    class GeometrySource(FakeSource):
        def __init__(self,root):
            super().__init__(root)
            folder=Path(root).parent/'GENERATED-original-calibrations';folder.mkdir(exist_ok=True)
            roles={}
            for role in ('leader','follower'):
                body=deepcopy(CALIBRATION)
                if role=='leader':body['wrist_roll']['drive_mode']=1
                raw=json.dumps(body,indent=2).encode();path=folder/(role+'.json')
                if path.exists():assert path.read_bytes()==raw
                else:path.write_bytes(raw)
                roles[role]=dict(port='SOFTWARE_NO_DEVICE_'+role,calibration=str(path))
                self.station.profiles[role]=dict(port=roles[role]['port'],calibration_path=str(path),
                    calibration_sha256=hashlib.sha256(raw).hexdigest(),verified_on_connected_hardware=True)
            self.station.configuration={'roles':roles};(Path(root)/'arms.json').write_text(json.dumps(self.station.configuration))
            follower=self.station.profiles['follower']
            follower['calibration_artifact']=configured_calibration_artifact(self.station,'follower',follower['calibration_sha256'])
    monkeypatch.setattr(original,'FakeSource',GeometrySource)
    store,chosen,sources=original.approved_cohort(tmp_path)
    original._assert_capture_pins(store,chosen,sources)
    assert chosen['spec']['recognized_type']=='pick_object_to_container'
    snapshots={str(path):original._sha(path) for _,path,raw,binding,review in sources}
    return store,chosen,sources,dict(original_source_manifest_sha256=snapshots,
        actual_physical_inputs=0,real_accepted_experts=0,new_optimizer_updates=0,
        input_kind='GENERATED_ORIGINAL_CAPTURE_WITH_PRESTART_CALIBRATION_ARTIFACT')
