"""Operator-authorized read-only hardware acceptance. Never writes servo registers."""
import argparse,dataclasses,fcntl,hashlib,json,subprocess,termios,time,traceback
from pathlib import Path
from so101_lab.resources import ROOT,ResourceLease,assert_hardware_available,snapshot
from so101_lab.hardware import CameraCapture,hardware_settings,hardware_paths,calibration_snapshot,JOINTS

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--confirm-read-only-hardware',action='store_true');parser.add_argument('--cameras',action='store_true');parser.add_argument('--buses',action='store_true');args=parser.parse_args()
    if not args.confirm_read_only_hardware:raise SystemExit('Requires explicit operator/user authorization')
    out=ROOT/'validation/acceptance-20261106';out.mkdir(exist_ok=True);report={'torque_register_writes':0,'goal_position_writes':0,'user_authorized_read_only':True,'resources_before':snapshot(),'cameras':{},'buses':{}}
    cfg=hardware_settings();capture={}
    with ResourceLease('hardware'):
        assert_hardware_available(hardware_paths(cfg))
        if args.cameras:
            folder=out/'cameras';folder.mkdir(exist_ok=True)
            for name,device in cfg['cameras'].items():
                cam=CameraCapture(name,device,cfg);before=subprocess.run(['v4l2-ctl','-d',device,'--list-ctrls'],capture_output=True,text=True).stdout
                try:
                    cam.start();start=time.monotonic();first=cam.sequence
                    time.sleep(2);frame=cam.latest_before(time.monotonic_ns());elapsed=time.monotonic()-start;fps=(cam.sequence-first)/elapsed
                    from PIL import Image
                    image=folder/(name+'.png');Image.fromarray(frame['image']).save(image)
                    capture[name]={**{k:v for k,v in frame.items() if k!='image'},'device':device,'path':image.name,'width':frame['image'].shape[1],'height':frame['image'].shape[0],'encoding':'lossless PNG decoded RGB','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
                    after=subprocess.run(['v4l2-ctl','-d',device,'--list-ctrls'],capture_output=True,text=True).stdout
                    report['cameras'][name]={'status':'PASS','frames_received':cam.sequence,'measured_fps':fps,'read_latency_ms':(frame['read_end_ns']-frame['read_start_ns'])/1e6,'controls_before':before,'controls_after':after,'controls_equal':before==after,'stream':cam.info,'last_capture':capture[name]}
                except Exception as e:report['cameras'][name]={'status':'FAIL','error':str(e),'traceback':traceback.format_exc()}
                finally:cam.close()
            (folder/'capture.json').write_text(json.dumps(capture,indent=2))
        if args.buses:
            from lerobot.motors import Motor,MotorCalibration,MotorNormMode
            from lerobot.motors.feetech import FeetechMotorsBus
            saved=calibration_snapshot()
            def forbidden(*a,**kw):raise RuntimeError('Register write forbidden in READ ONLY acceptance')
            for role in ('leader','follower'):
                motors={name:Motor(i,'sts3215',MotorNormMode.RANGE_0_100 if name=='gripper' else MotorNormMode.DEGREES) for i,name in enumerate(JOINTS,1)}
                calibration={name:MotorCalibration(**v) for name,v in saved['values'][role].items()}
                bus=FeetechMotorsBus(port=cfg[role+'_port'],motors=motors,calibration=calibration)
                bus.write=forbidden;bus.sync_write=forbidden;bus.enable_torque=forbidden;bus.disable_torque=forbidden
                try:
                    assert_hardware_available([cfg[role+'_port']]);bus.connect(handshake=False);fcntl.ioctl(bus.port_handler.ser.fileno(),termios.TIOCEXCL);bus._handshake()
                    actual=bus.read_calibration();raw=bus.sync_read('Present_Position',normalize=False,num_retry=0);by_id={bus.motors[n].id:v for n,v in raw.items()};normalized=bus._normalize(by_id)
                    torque={n:bus.read('Torque_Enable',n,normalize=False,num_retry=0) for n in JOINTS};mode={n:bus.read('Operating_Mode',n,normalize=False,num_retry=0) for n in JOINTS}
                    mismatches=[n for n in JOINTS if any(getattr(actual[n],k)!=getattr(calibration[n],k) for k in ('id','homing_offset','range_min','range_max'))]
                    report['buses'][role]={'status':'PASS' if not mismatches else 'FAIL','read_only':True,'present_raw_ticks':raw,'normalized_positions':{n:normalized[bus.motors[n].id] for n in JOINTS},'torque_enable_readback':torque,'operating_mode_readback':mode,'calibration_matches_snapshot':not mismatches,'mismatches':mismatches,'live_calibration':{n:dataclasses.asdict(c) for n,c in actual.items()},'units':['degrees']*5+['percent_calibrated_range'],'movement_or_direction_test':'NOT TESTED'}
                except Exception as e:report['buses'][role]={'status':'FAIL','error':str(e),'traceback':traceback.format_exc()}
                finally:
                    if bus.is_connected:bus.disconnect(disable_torque=False)
    report['resources_after']=snapshot();(out/'hardware-readonly.json').write_text(json.dumps(report,indent=2));print(json.dumps({'cameras':{k:v['status'] for k,v in report['cameras'].items()},'buses':{k:v['status'] for k,v in report['buses'].items()},'torque_writes':0,'goal_writes':0},indent=2))
if __name__=='__main__':main()
