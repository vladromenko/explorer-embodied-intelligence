#!/usr/bin/env python3
"""ENGINEERING_GLUE: finite registered Study export/restore qualification.

No physics, model, database, network, device or archived driver execution.
The independent child denies all original study-file opens at Python's audit
boundary before reconstructing the restored evidence and frozen benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

from explorer.study_registry import STUDY_ID,inspect_registered_study
from explorer_shared.study_bundle import (archive_study_bundle,export_study_bundle,
    restore_study_bundle,unpack_study_archive)
from explorer_shared.study_evidence import canonical,sha_file


def write(path,value):
    with path.open('xb') as stream:stream.write(canonical(value))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    parser.add_argument('--study-id',default=STUDY_ID);parser.add_argument('--restore-config',type=Path)
    args=parser.parse_args()
    if args.restore_config:
        config=json.loads(args.restore_config.read_bytes());denied=set(config['original_files']);blocked=[]
        def audit(event,arguments):
            if event=='open' and arguments and isinstance(arguments[0],(str,bytes,os.PathLike)):
                path=str(Path(os.fsdecode(arguments[0])).resolve())
                if path in denied:
                    blocked.append(path);raise PermissionError('Qualification forbids reading an original study file')
        sys.addaudithook(audit)
        receipt=restore_study_bundle(config['bundle'],config['destination'],expected_manifest_sha256=config['manifest_sha256'])
        receipt.update(original_read_guard='python_audit_open_deny_exact_original_paths',original_paths_denied=len(denied),blocked_original_open_attempts=len(blocked))
        write(Path(config['receipt']),receipt)
    else:
        if args.output is None:parser.error('--output new private directory required')
        output=args.output.resolve();output.mkdir(mode=0o700,parents=True,exist_ok=False)
        started=time.monotonic();inspection=inspect_registered_study(args.study_id)
        write(output/'inspection.json',inspection.document)
        exported=export_study_bundle(inspection,output/'bundle');write(output/'export-receipt.json',exported)
        archive=archive_study_bundle(output/'bundle',output/'study.tar',expected_manifest_sha256=exported['manifest_sha256'])
        write(output/'archive-receipt.json',archive)
        unpacked=unpack_study_archive(output/'study.tar',output/'independent-unpacked',expected_archive_sha256=archive['archive_sha256'],expected_manifest_sha256=exported['manifest_sha256'])
        write(output/'unpack-receipt.json',unpacked)
        restore_config=dict(bundle=str(output/'independent-unpacked'),destination=str(output/'independent-restored'),
            manifest_sha256=exported['manifest_sha256'],original_files=sorted(str(path.resolve()) for path in inspection.sources.values()),receipt=str(output/'independent-restore-receipt.json'))
        write(output/'restore-config.json',restore_config)
        command=[sys.executable,str(Path(__file__).resolve()),'--restore-config',str(output/'restore-config.json')]
        child=subprocess.run(command,check=False,capture_output=True,text=True,timeout=120)
        (output/'independent-child.stdout').write_text(child.stdout);(output/'independent-child.stderr').write_text(child.stderr)
        if child.returncode:raise RuntimeError('Independent Study restore child failed; original evidence preserved')
        restore=json.loads((output/'independent-restore-receipt.json').read_bytes())
        # Recheck every original after all export/restore reads. No original may
        # be changed by the qualification or by background source mutation.
        unchanged=all(sha_file(inspection.sources[row['logical_path']])==row['sha256'] for row in inspection.document['file_manifest'])
        if not unchanged:raise RuntimeError('Original study changed during qualification')
        repository=Path(__file__).resolve().parents[1];source=output/'qualification-source';source.mkdir()
        source_files=['shared/explorer_shared/study_evidence.py','shared/explorer_shared/study_bundle.py',
            '00-control-center/src/explorer/study_registry.py','scripts/qualify_registered_study.py']
        source_sha={}
        for name in source_files:
            payload=(repository/name).read_bytes();destination=source/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(payload)
            source_sha[name]=hashlib.sha256(payload).hexdigest()
        head=subprocess.run(['git','rev-parse','HEAD'],cwd=repository,capture_output=True,text=True,check=True).stdout.strip()
        report=dict(schema='explorer_registered_study_portability_qualification_v1',study_id=args.study_id,
            implementation_source_commit=head,implementation_sha256=source_sha,qualification_runtime=dict(python=platform.python_version(),executable=sys.executable),
            elapsed_s=time.monotonic()-started,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            original_files_unchanged=unchanged,original_files=len(inspection.sources),
            inspection_sha256=inspection.document['inspection_sha256'],export=exported,archive=archive,independent_restore=restore,
            outcomes={row['attempt_id']:row['outcome'] for row in inspection.document['attempts']},
            benchmark_recomputed=restore['benchmark_recomputed'],completeness=inspection.document['completeness'],
            original_reexecution_dependencies_closed=False,no_new_physics_or_optimizer=True,physical_io=False,master_prompt_completed=False)
        write(output/'qualification.json',report)
        print(json.dumps(dict(output=str(output),qualification_sha256=sha_file(output/'qualification.json'),completeness=report['completeness'],files=report['original_files'],elapsed_s=report['elapsed_s']),sort_keys=True))


if __name__=='__main__':main()
