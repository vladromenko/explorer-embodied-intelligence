"""Two-clock cached source recording over actual localhost authenticated HTTP."""
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
import time
import tarfile

import pytest

from explorer_shared.cooperative_recording05 import (CachedNodeObservation,CooperativeRecorder,sha_file,
                                                    import_cooperative_recording)

TOKEN='local-fake-observer-token-only'


@pytest.fixture
def nodes():
    calls=[];servers=[];jpeg={'giver':b'\xff\xd8original-giver-cache\xff\xd9',
                           'receiver':b'\xff\xd8original-receiver-cache\xff\xd9'}
    def serve(role,domain,tick):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                calls.append((role,'GET',self.path,self.headers.get('Authorization')))
                if self.headers.get('Authorization')!='Bearer '+TOKEN:
                    self.send_error(401);return
                if self.path.startswith('/cameras/'):
                    raw=jpeg[role];self.send_response(200);self.send_header('Content-Type','image/jpeg')
                    for key,value in {'X-Frame-ID':role+'-one-original','X-Clock-Domain':domain,
                                      'X-Monotonic-NS':str(tick),'X-Timestamp-Basis':'original-cache-rx-estimate'}.items():
                        self.send_header(key,value)
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                else:
                    value=({'schema':'explorer_native_dispatch_page_v1','next_sequence':1,'receipts':[],'gap':False,
                            'clock_domain':domain,'boot_id':domain.split(':')[-1]}
                           if role=='giver' else {'measurements':{'follower':[0]*6,'clock_domain':domain,'monotonic_ns':tick},
                                                 'last_command':None})
                    raw=json.dumps(value,indent=2).encode();self.send_response(200)
                    self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                    self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                calls.append((role,'POST',self.path,None));self.send_error(405)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start();servers.append(server)
        return CachedNodeObservation('http://127.0.0.1:'+str(server.server_port),TOKEN,role)
    sources={'giver':serve('giver','nano:nano-boot',9_000_000_000),
             'receiver':serve('receiver','pi:pi-boot',100)}
    try:yield sources,calls,jpeg
    finally:
        for server in servers:server.shutdown();server.server_close()


def wait_for(predicate):
    until=time.monotonic()+4
    while not predicate() and time.monotonic()<until:time.sleep(.01)
    assert predicate()


def test_cached_http_sources_preserve_exact_bytes_and_independent_clocks_without_control_posts(tmp_path,nodes):
    sources,calls,jpegs=nodes;recorder=CooperativeRecorder(tmp_path,sources,clock_domain='nx:nx-boot',poll_s=.04)
    try:
        result=recorder.start({'session_id':'original-session','scene_id':'actual-declared-scene','split':'train'})
        wait_for(lambda:recorder.status()['sample_packets']>=2)
        recorder.stage('RECEIVER_GRASP_CHECK',{'receiver':{'phase_id':'grasp','clock_domain':'pi:pi-boot','monotonic_ns':200}})
        recorder.finish({'status':'unknown','method':'explicit_unknown_no_hold_inference'})
        assert recorder.done.wait(4);folder=tmp_path/result['recording_id']
        manifest=json.loads((folder/'manifest.json').read_bytes())
        assert manifest['state']=='SEALED' and manifest['analysis_only'] and not manifest['bc_targets_constructed']
        packet=manifest['samples'][0]['roles']
        assert packet['giver']['source_monotonic_ns']==9_000_000_000
        assert packet['receiver']['source_monotonic_ns']==100
        assert packet['giver']['source_clock_domain']!=packet['receiver']['source_clock_domain']
        assert not manifest['clock_domains_equated']
        for role in ('giver','receiver'):
            assert (folder/packet[role]['jpeg_source_file']).read_bytes()==jpegs[role]
            telemetry=(folder/packet[role]['telemetry_source_file']).read_bytes()
            assert telemetry.startswith(b'{\n')  # preserve source formatting, no JSON reconstruction
            assert manifest['source_files'][packet[role]['telemetry_source_file']]['sha256']==hashlib.sha256(telemetry).hexdigest()
        info=json.loads((folder/'archive-info.json').read_bytes())
        assert info['archive_sha256']==sha_file(folder/'recording.tar')
        assert all(method=='GET' and auth=='Bearer '+TOKEN for _,method,_,auth in calls)
        assert manifest['phase_events'][0]['coordinator_phase_is_not_source_exposure_label']
        imported=import_cooperative_recording(folder/'recording.tar',tmp_path/'imported',expected_sha256=info['archive_sha256'])
        assert not imported['admitted_for_training'] and not imported['dataset_action_spaces_merged']
        original=(tmp_path/'imported'/'source'/packet['giver']['jpeg_source_file']).read_bytes()
        assert original==jpegs['giver'] and sha_file(tmp_path/'imported'/'original-recording.tar')==info['archive_sha256']
    finally:recorder.close()


def test_finish_drains_inflight_get_but_never_relabels_late_response_as_pre_finish(tmp_path,nodes):
    sources,calls,jpegs=nodes;entered,release=threading.Event(),threading.Event()
    original=sources['receiver'].snapshot
    def slow(cursor):entered.set();assert release.wait(3);return original(cursor)
    sources['receiver'].snapshot=slow
    recorder=CooperativeRecorder(tmp_path,sources,clock_domain='nx:nx-boot',poll_s=.05)
    try:
        result=recorder.start({'session_id':'episode'});assert entered.wait(2)
        recorder.finish({'status':'unknown'})
        assert not recorder.done.wait(.1)
        release.set();assert recorder.done.wait(3)
        manifest=json.loads((tmp_path/result['recording_id']/'manifest.json').read_bytes())
        assert len(manifest['samples'])==1 and manifest['samples'][0]['late_after_finish']
        assert manifest['samples'][0]['phase_at_sampling_request']=='PREPARE'
        assert manifest['finish_coordinator_ns']<manifest['samples'][0]['responses_received_coordinator_ns']
    finally:release.set();recorder.close()


def test_no_capture_gets_before_start_no_auto_resume_and_no_loop_speedup(tmp_path,nodes):
    sources,calls,jpegs=nodes;recorder=CooperativeRecorder(tmp_path,sources,clock_domain='nx',poll_s=.1)
    time.sleep(.12);assert calls==[]
    result=recorder.start({'session_id':'sample'});time.sleep(.24)
    recorder.finish({'status':'unknown'});assert recorder.done.wait(3)
    assert len([row for row in calls if row[2].startswith('/cameras/')])<=6
    recorder.close();count=len(calls);time.sleep(.12);assert len(calls)==count
    with pytest.raises(ValueError):recorder.start({'session_id':'old'})


def test_raw_import_rejects_archive_traversal_and_duplicate_members(tmp_path):
    for index,names in enumerate((['../escape'],['same','same'])):
        path=tmp_path/(str(index)+'.tar')
        with tarfile.open(path,'w') as archive:
            for name in names:
                entry=tarfile.TarInfo(name);entry.size=1;archive.addfile(entry,io.BytesIO(b'x'))
        with pytest.raises(ValueError,match='Unsafe or duplicate'):
            import_cooperative_recording(path,tmp_path/('out'+str(index)),expected_sha256=sha_file(path))
    assert not (tmp_path/'escape').exists()
