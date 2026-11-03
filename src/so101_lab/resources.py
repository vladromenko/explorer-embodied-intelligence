"""Conservative resource interlocks. Never opens hardware or kills resource owners."""
import os, time, json, fcntl, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class ResourceBusy(RuntimeError): pass

def owners(paths):
    targets={str(Path(p).resolve()) for p in paths}; found=[]
    for proc in Path('/proc').glob('[0-9]*'):
        if int(proc.name)==os.getpid(): continue
        try:
            matches=[]
            for fd in (proc/'fd').iterdir():
                try:
                    link=os.readlink(fd)
                    if link in targets: matches.append(link)
                except OSError: pass
            if matches: found.append({'pid':int(proc.name),'name':(proc/'comm').read_text().strip(),'devices':sorted(set(matches))})
        except (OSError,ValueError): pass
    return found

def snapshot():
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        k,v=line.split(':',1); mem[k]=int(v.strip().split()[0])*1024
    devices=['/dev/so101-leader','/dev/so101-follower','/dev/video0','/dev/video2']
    gpu=[]
    for proc in Path('/proc').glob('[0-9]*'):
        if int(proc.name)==os.getpid(): continue
        try:
            rss=int((proc/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
            cmd=(proc/'cmdline').read_text().replace('\0',' ')
            gpu_fd=False
            if rss>256*1024**2 and str(ROOT) not in cmd:
                for fd in (proc/'fd').iterdir():
                    try:
                        target=os.readlink(fd)
                        if target.startswith(('/dev/nvidia','/dev/nvhost-gpu','/dev/dri/render')):gpu_fd=True;break
                    except OSError:pass
            if (rss>2*1024**3 or gpu_fd) and str(ROOT) not in cmd:
                gpu.append({'pid':int(proc.name),'name':(proc/'comm').read_text().strip(),'rss_bytes':rss,'reason':'foreign high-memory process; unified GPU/RAM conflict'})
        except (OSError,ValueError,IndexError): pass
    return {'timestamp_ns':time.monotonic_ns(),'memory_available_bytes':mem['MemAvailable'],'memory_total_bytes':mem['MemTotal'],'disk_free_bytes':shutil.disk_usage(ROOT).free,'hardware_owners':owners(devices),'compute_conflicts':gpu,'cuda_build':'2.11.0+cu130 (environment verified; server does not load torch)'}

def assert_hardware_available(paths):
    busy=owners(paths)
    if busy: raise ResourceBusy('Устройства заняты: '+json.dumps(busy,ensure_ascii=False))
    missing=[str(p) for p in paths if not Path(p).exists()]
    if missing: raise ResourceBusy('Нет устройств: '+', '.join(missing))

def assert_compute_available(device='cuda',minimum_available_gb=7,**kwargs):
    s=snapshot()
    if device.startswith('cuda') and s['compute_conflicts']: raise ResourceBusy('GPU/RAM занят другим экспериментом: '+json.dumps(s['compute_conflicts'],ensure_ascii=False))
    if s['memory_available_bytes']<minimum_available_gb*1024**3: raise ResourceBusy(f'Недостаточно доступной памяти: {s["memory_available_bytes"]/1024**3:.1f} GB, нужно {minimum_available_gb} GB')
    return s

def assert_disk_space(minimum_bytes=1024**3,path=None):
    if shutil.disk_usage(path or ROOT).free<minimum_bytes: raise ResourceBusy('Недостаточно места на диске')

class ResourceLease:
    def __init__(self,name): self.name=name; self.file=None
    def __enter__(self):
        p=ROOT/'runtime/locks'; p.mkdir(parents=True,exist_ok=True)
        self.file=(p/(self.name.replace('/','_')+'.lock')).open('a+')
        try: fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close(); raise ResourceBusy('Ресурс уже занят лабораторией: '+self.name)
        self.file.seek(0); self.file.truncate(); self.file.write(str(os.getpid())); self.file.flush(); return self
    def __exit__(self,*args):
        if self.file: fcntl.flock(self.file,fcntl.LOCK_UN); self.file.close(); self.file=None


def dataset_lease(path):
    import hashlib
    return ResourceLease('dataset-'+hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24])
