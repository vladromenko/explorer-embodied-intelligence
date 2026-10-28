"""Reconnect starts only the existing dead worker, never a second bus owner."""
from types import SimpleNamespace
import threading
from explorer_shared.station_connection import connect_station


def test_user_reconnect_reuses_same_station_worker_after_power_off():
    station=SimpleNamespace(lock=threading.RLock(),mode='idle',record=None,finishing_record=None,
        stop_event=threading.Event(),buses={},owners=[],profiles={},measurements={},error='offline')
    station.thread=threading.Thread(target=lambda:None)
    station.status=lambda:dict(available=len(station.profiles)==2,error=station.error)
    def original_work():station.profiles.update(leader={},follower={})
    station._work=original_work
    assert connect_station(station)['available']
    station.thread.join(1)
    previous=station.thread
    assert connect_station(station)['available'] and station.thread is previous
