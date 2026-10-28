"""Explicit user reconnect of the SAME frozen Station after an unplug/power-off.

Reuses its worker, buses/calibration and writer. No controller command, torque,
calibration write or second serial owner is introduced.
"""
import threading
import time


def connect_station(station,timeout=6):
    with station.lock:
        if station.status()['available']:return station.status()
        if station.thread.is_alive():raise ValueError('Руки подключаются. Повторите через несколько секунд.')
        if station.mode!='idle' or station.record or station.finishing_record:
            raise ValueError('Завершите текущую запись или управление перед подключением.')
        if any(bus.is_connected for bus in station.buses.values()) or any(not owner.closed for owner in station.owners):
            raise ValueError('Предыдущий владелец USB ещё подключён.')
        if station.stop_event.is_set():raise ValueError('Сервис рук завершён.')
        station.buses.clear();station.owners.clear();station.profiles.clear();station.measurements={}
        station.error=None;station.last_hold_error=None
        station.thread=threading.Thread(target=station._work,name='so101-station',daemon=True)
        station.thread.start()
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        status=station.status()
        if status['available']:return status
        if status['error']:raise ValueError('Нет связи с руками. Проверьте питание обеих SO-101 и USB.')
        time.sleep(.05)
    raise ValueError('Руки ещё подключаются. Проверьте питание и USB и нажмите «Управлять» ещё раз.')
