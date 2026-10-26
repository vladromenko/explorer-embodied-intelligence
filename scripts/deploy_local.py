#!/usr/bin/env python3
"""Deploy commit-addressed Explorer services; never alter onboard controller."""
import argparse
import json
import shlex
import socket
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remote(host, args, data=None):
    local = host == socket.gethostname()
    command = args if local else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',host,shlex.join(args)]
    result = subprocess.run(command,
        input=data, capture_output=True,cwd=Path.home() if local else None)
    if result.returncode:
        raise RuntimeError(host + ': ' + result.stderr.decode(errors='replace')[-1200:])
    return result.stdout


INSTALL = '''import io,os,pathlib,re,sys,tarfile
b=pathlib.Path.home()/'.local/share/explorer'
release=b/'releases'/sys.argv[1]
release.parent.mkdir(parents=True,exist_ok=True)
b.chmod(0o755)
release.parent.chmod(0o755)
if not release.exists():
 stage=release.with_name(release.name+'.staging')
 stage.mkdir(exist_ok=False)
 with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()),mode='r:gz') as tar:tar.extractall(stage,filter='data')
 (stage/'RELEASE_COMMIT').write_text(sys.argv[1]+'\\n')
 stage.rename(release)
if len(sys.argv)>2 and sys.argv[2]=='stage-only':
 for entry in [release,*release.rglob('*')]:
  if not entry.is_symlink():entry.chmod(0o755 if entry.is_dir() or entry.stat().st_mode&0o111 else 0o644)
 print(release)
 sys.exit(0)
current=b/'current'
sources=[release]
if current.is_symlink():sources.append(current.resolve())
for source in sources:
 if source.parent==release.parent and re.fullmatch('[0-9a-f]{40}',source.name) and (source/'RELEASE_COMMIT').read_text().strip()==source.name:
  for entry in [source,*source.rglob('*')]:
   if not entry.is_symlink():entry.chmod(0o755 if entry.is_dir() or entry.stat().st_mode&0o111 else 0o644)
if current.is_symlink():
 (b/'previous-release').write_text(str(current.resolve()))
 (b/'previous-release').chmod(0o644)
temporary=b/'current.next'
if temporary.is_symlink():temporary.unlink()
temporary.symlink_to(release,target_is_directory=True)
os.replace(temporary,current)
print(release)
'''


WRITE = '''import os,pathlib,sys
p=pathlib.Path.home()/sys.argv[1]
p.parent.mkdir(parents=True,exist_ok=True)
p.write_bytes(sys.stdin.buffer.read())
os.chmod(p,int(sys.argv[2],8))
'''


def write(host, relative_path, content, mode='600'):
    remote(host, ['python3','-c',WRITE,relative_path,mode], content.encode())


def service(host, name, source):
    write(host, '.config/systemd/user/' + name + '.service', source, '644')
    remote(host, ['systemctl','--user','daemon-reload'])
    remote(host, ['systemctl','--user','enable',name + '.service'])
    remote(host, ['systemctl','--user','restart',name + '.service'])
    print(host + ': ' + remote(host, ['systemctl','--user','is-active',name + '.service']).decode().strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nodes', nargs='*', default=['homebrain','explorer-pi','explorer'])
    parser.add_argument('--dry-run', action='store_true', help='Print the exact managed-service scope without SSH or writes')
    parser.add_argument('--stage-only',action='store_true',help='Install immutable source only; do not switch current or restart services')
    args = parser.parse_args()
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if args.dry_run:
        print(json.dumps({'read_only':True,'commit':commit,'nodes':args.nodes,'actions':['install commit-addressed source','preserve existing private configuration','activate only Explorer managed user services','refresh previously adopted CPU camera cache only'],
                          'existing_controller_settings_changed':False,'existing_camera_controller_mcu_restarted':False},indent=2))
        return
    archive = subprocess.check_output(['git','archive','--format=tar.gz',commit],cwd=ROOT)
    for host in args.nodes:
        remote(host, ['python3','-c',INSTALL,commit]+(['stage-only'] if args.stage_only else []), archive)
        print(host + ': release ' + commit[:12])
    if args.stage_only:
        print('Source staged; current symlinks and all services unchanged.')
        return
    main_env = 'PYTHONPATH=$HOME/.local/share/explorer/current/00-control-center/src:$HOME/.local/share/explorer/current/shared'
    init = 'import pathlib,subprocess,os\nh=pathlib.Path.home();b=h/".local/share/explorer";env=dict(os.environ,PYTHONPATH=str(b/"current/00-control-center/src")+":"+str(b/"current/shared"));c=h/".config/explorer/config.json"\nif not c.exists():subprocess.run([str(b/"venv/bin/python"),"-m","explorer.cli","init"],env=env,check=True)'
    remote('homebrain', ['python3','-c',init])
    config = json.loads(remote('homebrain', ['cat','.config/explorer/config.json']))
    unit = '''[Unit]
Description=Explorer {title}
After=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH=%h/.local/share/explorer/current/00-control-center/src:%h/.local/share/explorer/current/shared
Environment=EXPLORER_SIM_MESH_DIR=%h/.local/share/explorer/state/assets/m3_reference/meshes
Environment=MUJOCO_GL=egl
WorkingDirectory=%h/.local/share/explorer/current
ExecStart=%h/.local/share/explorer/venv/bin/python -m explorer.cli {command}
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
KillMode=control-group

[Install]
WantedBy=default.target
'''
    if 'homebrain' in args.nodes:
        service('homebrain','explorer-control-center',unit.format(title='Control Center',command='serve'))
        service('homebrain','explorer-worker',unit.format(title='durable job worker',command='worker'))
        launcher = '#!/bin/sh\nexport PYTHONPATH="$HOME/.local/share/explorer/current/00-control-center/src:$HOME/.local/share/explorer/current/shared"\nexec "$HOME/.local/share/explorer/venv/bin/python" -m explorer.cli "$@"\n'
        write('homebrain','.local/bin/explorerctl',launcher,'700')
    for node in config['nodes']:
        host = 'explorer-pi' if node['id'] == 'pi' else 'explorer'
        if host in args.nodes:
            env = 'EXPLORER_NODE_TOKEN=' + node['token'] + '\nEXPLORER_INGEST_TOKEN=' + config['ingest_token'] + '\nEXPLORER_CENTRAL_URL=' + config['public_url'] + '\n'
            if node['id']=='pi' and node.get('hailo_model'):
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+',node['hailo_model']):raise ValueError('A literal absolute Hailo asset path is required')
                env+='EXPLORER_HAILO_MODEL='+node['hailo_model']+'\n'
            write(host,'.config/explorer/node.env',env)
            node_unit = '''[Unit]
Description=Explorer camera and event agent
After=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH=%h/.local/share/explorer/current/shared
EnvironmentFile=%h/.config/explorer/node.env
ExecStart={python} -m explorer_shared.node_agent --mode {mode} --node-id {mode} --port {port} --state-dir %h/.local/share/explorer/node-state
Restart=on-failure
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=default.target
'''.format(mode=node['id'],port=node['port'],python='%h/.local/share/explorer/servo-venv/bin/python' if node['id']=='pi' else '/usr/bin/python3')
            service(host,'explorer-observer',node_unit)
            if node['id']=='nano':
                adopted=remote(host,['python3','-c','import pathlib;h=pathlib.Path.home();print(int((h/".local/state/explorer/native-perception-unit-20261026/receipt.json").is_file() and (h/".config/systemd/user/explorer-perception.service").is_symlink() and (h/".config/systemd/user/explorer-perception.service").resolve()==pathlib.Path("/dev/null")))']).strip()==b'1'
                if adopted:
                    service(host,'explorer-nano-camera-cache',(ROOT/'scripts/explorer-nano-camera-cache.service').read_text())
    print('Deployed. Existing ROSMASTER controller settings were not written or restarted.')


if __name__ == '__main__':
    main()
