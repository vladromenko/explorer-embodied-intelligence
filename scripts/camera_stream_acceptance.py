"""Explicitly authorized camera-only simultaneous capture, no servo imports/commands."""
import argparse,json,time,hashlib,subprocess,traceback
from pathlib import Path
from so101_lab.resources import ROOT,ResourceLease,assert_hardware_available
from so101_lab.hardware import CameraCapture,hardware_settings

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
    if not a.execute:raise SystemExit('Explicit --execute required')
    folder=ROOT/'validation/acceptance-20261106/cameras-simultaneous';folder.mkdir(exist_ok=True)
    report={'status':'running','torque_commands':0,'goal_commands':0,'cameras':{}};cfg=hardware_settings();cams={}
    with ResourceLease('hardware'):
        assert_hardware_available(list(cfg['cameras'].values()))
        try:
            for name,device in cfg['cameras'].items():
                cam=CameraCapture(name,device,cfg);cams[name]=cam;cam.start()
            initial={n:c.sequence for n,c in cams.items()};started=time.monotonic();time.sleep(5);duration=time.monotonic()-started;cutoff=time.monotonic_ns()
            from PIL import Image
            capture={}
            for name,cam in cams.items():
                frame=cam.latest_before(cutoff)
                if frame is None or cam.error:raise RuntimeError(f'{name}: {cam.error or "missing frame"}')
                fps=(cam.sequence-initial[name])/duration;image=folder/(name+'.png');Image.fromarray(frame['image']).save(image)
                age=(cutoff-frame['timestamp_ns'])/1e6
                capture[name]={**{k:v for k,v in frame.items() if k!='image'},'path':image.name,'device':cam.device,'width':frame['image'].shape[1],'height':frame['image'].shape[0],'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
                report['cameras'][name]={'status':'PASS' if fps>=24 and age<100 else 'FAIL','measured_fps':fps,'frames_received':cam.sequence,'latest_age_ms':age,'error':cam.error,'stream':cam.info}
            report['camera_skew_ms']=abs(capture['top']['timestamp_ns']-capture['wrist']['timestamp_ns'])/1e6
            (folder/'capture.json').write_text(json.dumps(capture,indent=2))
            report['status']='PASS' if all(c['status']=='PASS' for c in report['cameras'].values()) and report['camera_skew_ms']<100 else 'FAIL'
        except Exception as e:report.update(status='FAIL',error=str(e),traceback=traceback.format_exc())
        finally:
            for cam in cams.values():cam.close()
    (folder/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));return 0 if report['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
