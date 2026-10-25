import threading
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
import explorer_shared.so101_station as module
from explorer_shared.so101_station import SO101Station,NAMES


class Bus:
    def __init__(self):
        self.writes=[]
    def sync_read(self,name,normalize=True):
        return {motor:(0 if name=='Torque_Enable' else 2048) for motor in NAMES}
    def sync_write(self,name,values,normalize=True):
        self.writes.append((name,values,normalize))


def test_start_holds_actual_position_before_enabling_torque_without_configuration_writes():
    station=SO101Station.__new__(SO101Station)
    station.bus_lock=threading.RLock()
    station.buses={'follower':Bus()}
    station._enable_hold()
    writes=station.buses['follower'].writes
    assert [row[0] for row in writes]==['Goal_Position','Torque_Enable']
    assert all(value==2048 for value in writes[0][1].values())
    assert all(value==1 for value in writes[1][1].values())
    assert all(row[2] is False for row in writes)


def test_goal_write_preserves_original_calibration_and_motor_parameters():
    station=SO101Station.__new__(SO101Station)
    station.bus_lock=threading.RLock()
    station.buses={'follower':Bus()}
    station._write([0,0,0,0,0,50])
    assert len(station.buses['follower'].writes)==1
    assert station.buses['follower'].writes[0][0]=='Goal_Position'


def make_station(tmp_path,monkeypatch):
    configuration=tmp_path/'arms.json'
    configuration.write_text(json.dumps({'roles':{}}))
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda path,*args,**kwargs:'test-boot' if str(path)=='/proc/sys/kernel/random/boot_id' else original(path,*args,**kwargs))
    station=SO101Station(configuration,tmp_path,SimpleNamespace(latest=lambda:True))
    station.profiles={'leader':{},'follower':{}}
    station._positions=lambda role:[0,0,0,0,0,50]
    station.buses={'follower':Bus()}
    return station


def test_request_prepared_before_stop_cannot_restart_motors(tmp_path,monkeypatch):
    station=make_station(tmp_path,monkeypatch)
    previous=station.control_epoch
    station.command('stop',{})
    with pytest.raises(ValueError,match='changed after'):
        station.command('teleop/start',{'observing':True,'expected_control_epoch':previous})
    assert station.buses['follower'].writes==[]
    station.mode='policy'
    station.command('stop',{})
    with pytest.raises(ValueError,match='ended or superseded'):
        station.command('action',{'observing':True,'control_epoch':previous,'sequence':1})
    assert station.mode=='idle'


def test_slow_record_checkpoint_does_not_block_stop_and_finish_is_idempotent(tmp_path,monkeypatch):
    station=make_station(tmp_path,monkeypatch)
    station.mode='teleoperation'
    station.measurements={'monotonic_ns':time.monotonic_ns()}
    station._start_record({'project_id':'personal'})
    entered,release=threading.Event(),threading.Event()
    original=module.save
    def delayed(path,value):
        if len(value.get('samples',[]))==10 and value.get('state')=='recording':
            entered.set()
            assert release.wait(3)
        return original(path,value)
    monkeypatch.setattr(module,'save',delayed)
    writer=threading.Thread(target=station._record_writer,daemon=True)
    writer.start()
    stamp=time.monotonic_ns()
    frame=SimpleNamespace(jpeg=b'actual-test-frame-bytes',frame_id='test:1',monotonic_ns=stamp)
    try:
        for index in range(10):
            measured=stamp+index*100_000_000
            station._sample(frame,[0]*6,[0]*6,[0]*6,measured,measured+1)
        assert entered.wait(2)
        before=time.monotonic()
        station.command('stop',{})
        assert time.monotonic()-before<.25
        assert station.mode=='idle'
    finally:
        release.set()
    result=station.command('record/finish',{'outcome':'unknown'})
    assert result['samples']==10
    assert station.command('record/finish',{'outcome':'unknown'})==result
    saved=json.loads(Path(result['manifest']).read_text())
    assert saved['samples'][0]['state_monotonic_ns']<=saved['samples'][0]['action_monotonic_ns']
    station.record_queue.put(None)
    writer.join(2)
