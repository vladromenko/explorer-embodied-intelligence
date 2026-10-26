#!/usr/bin/env python3
"""ENGINEERING_GLUE: read-only saved V9 evidence → new diagnosis/probe protocol."""
import argparse
import os
from pathlib import Path
from explorer_shared.contact_diagnosis import canonical, inspect_campaign, probe_protocol, sha


def write_new(path, value):
    with path.open('xb') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--campaign-receipt-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--new-campaign-id', required=True)
    args = parser.parse_args()
    analysis = inspect_campaign(args.campaign, expected_receipt_sha256=args.campaign_receipt_sha256)
    protocol = probe_protocol(analysis, campaign_id=args.new_campaign_id)
    protocol['analysis_driver_sha256'] = sha(Path(__file__).read_bytes())
    protocol['protocol_content_sha256'] = sha(canonical(protocol))
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_new(args.output/'saved-contact-diagnosis.json', analysis)
    write_new(args.output/'proposed-probe-protocol.json', protocol)
    print(canonical(dict(attempts_read=len(analysis['attempts']), diagnosis=analysis['diagnosis'],
        analysis_sha256=sha(canonical(analysis)), protocol_sha256=sha(canonical(protocol)),
        new_dynamics_steps=0, physical_io=False)).decode())


if __name__ == '__main__':
    main()
