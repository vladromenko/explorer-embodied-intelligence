"""One physical RGBD camera on Nano and one Logitech on Pi, no device opened."""
import json
from pathlib import Path
from types import SimpleNamespace

from explorer_shared.camera_inventory import audited_camera_metadata,AUDIT
from explorer_shared.node_agent import NodeAgent


def test_confirmed_two_physical_cameras_have_distinct_domains_and_no_required_second_02_view():
    body=json.loads(AUDIT.read_bytes());assert body['physical_camera_count']==len(body['cameras'])==2
    nano,pi=body['cameras'];assert nano['identity']['model']=='DaBai DCW2' and nano['capabilities']['actual_02_technical_recording_frames']==17
    assert nano['identity']['identity_is_one_RGBD_camera_not_two_physical_cameras']
    assert not nano['second_rosmaster_camera_required'] and not pi['required_for_02']
    assert pi['identity']['manufacturer']=='Logitech' and pi['identity']['model'] is None
    assert nano['camera_domain']!=pi['camera_domain'] and not pi['may_substitute_for_rosmaster_camera']
    assert not pi['shared_scene_verified'] and not pi['cross_camera_synchronization_verified']


def test_inventory_does_not_invent_camera_or_override_actual_stream_availability():
    for role,stream in [('nano','nano-rgbd'),('pi','pi-overview')]:
        source=SimpleNamespace(metadata=lambda stream=stream:dict(id=stream,available=False,error='actual_frame_unavailable'))
        agent=SimpleNamespace(mode=role,sources={stream:source})
        cameras=NodeAgent.cameras(agent);assert len(cameras)==1 and cameras[0]['id']==stream
        assert cameras[0]['available'] is False and cameras[0]['error']=='actual_frame_unavailable'
        assert cameras[0]['identity_audit']['runtime_device_probe_performed'] is False
    assert audited_camera_metadata('nano','nano-logitech')=={}


def test_central_projects_legacy_phantom_without_mutating_observed_inventory(tmp_path,monkeypatch):
    import importlib
    import json
    from explorer.store import Store
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state')
    store.update_node('nano',{'cameras':[{'id':'nano-rgbd','available':True},{'id':'nano-logitech','available':False}]})
    store.update_node('pi',{'cameras':[{'id':'pi-overview','available':True},{'id':'pi-overview','available':True}]})
    before=json.dumps(store.nodes(),sort_keys=True)
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[dict(id=role,name=role,host='memory.test') for role in ('nano','pi')]))
    nodes={row['id']:row for row in module.node_states()}
    assert len(nodes['nano']['cameras'])==len(nodes['pi']['cameras'])==1
    nano=nodes['nano']['cameras'][0];pi=nodes['pi']['cameras'][0]
    assert nano['id']=='nano-rgbd' and nano['identity_audit']['identity']['model']=='DaBai DCW2'
    assert pi['id']=='pi-overview' and 'Logitech' in pi['label']
    assert nano['available'] is pi['available'] is True
    assert json.dumps(store.nodes(),sort_keys=True)==before
