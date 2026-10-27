#!/usr/bin/env python3
"""Read-only saved leader trace analysis. Never connects to a robot or client."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared'))
from explorer_shared.retarget_continuity import qualify_recorded_trace
from explorer_shared.retarget_recording import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path);parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args();trace=options.trace.resolve();root=trace.parent;raw=trace.read_bytes();document=json.loads(raw)
    sources={}
    for row in document['samples']:
        name=row['raw_source_file'];path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size>256*1024:
            raise ValueError('Original source must be bounded local regular contained bytes')
        sources[name]=path.read_bytes()
    result=qualify_recorded_trace(raw,sources,state_dir=options.output.parent/'unused-memory-sink')
    options.output.parent.mkdir(parents=True,exist_ok=True);write_json(options.output,result)
    print(json.dumps({'report':str(options.output),'trace_sha256':result['trace_sha256'],
        'samples':result['source_samples'],'changing_input_observed':result['changing_input_observed'],
        'full_continuity_qualification':result['full_continuity_qualification'],'physical_commands':0}))


if __name__=='__main__':main()
