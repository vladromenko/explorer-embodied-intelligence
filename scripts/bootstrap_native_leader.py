#!/usr/bin/env python3
"""ENGINEERING_GLUE: Export a deterministic03 registration proposal; reads files only, no devices."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'shared'))
from explorer_shared.native_leader_bootstrap import bootstrap_geometry, FRAME_YAW
from explorer_shared.native_leader_registration import NativeLeaderContext
from explorer_shared.retarget_recording import canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calibration', type=Path, required=True, help='Existing original LeRobot leader calibration, unchanged')
    parser.add_argument('--frame-convention', choices=FRAME_YAW, default='aligned_urdf_base_axes')
    parser.add_argument('--output', type=Path, required=True, help='New proposal file; never replaces an original')
    args = parser.parse_args()
    context = NativeLeaderContext(ROOT, args.calibration)
    geometry = bootstrap_geometry(context, frame_convention=args.frame_convention)
    raw = canonical(geometry.registration)
    with args.output.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    print('BOOTSTRAP_DERIVED ' + geometry.registration['sha256'] + ' physical_acceptance=false physical_commands=0')


if __name__ == '__main__':main()
