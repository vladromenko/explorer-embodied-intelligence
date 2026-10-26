#!/usr/bin/env python3
"""ENGINEERING_GLUE: derive browser and JSON identities from one semantic registry."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'shared'))
from explorer_shared.experiments import registry_document


def generated_files(root):
    registry=registry_document()
    browser='''"use strict";
// ENGINEERING_GLUE: generated from explorer_shared.experiments; never edit numeric IDs here.
const ExplorerExperimentRegistry = Object.freeze(REGISTRY_DOCUMENT);
const experimentId = slug => { const row = ExplorerExperimentRegistry.projects.find(item => item.slug === slug); if (!row) throw new Error("Unknown semantic experiment"); return row.id; };
const SO101_SOURCE_PROJECT = experimentId("so101_source_learning");
const TRANSFER_PROJECT = experimentId("cross_embodiment_transfer");
const SO101_SOURCE_ROUTE = `/experiments/${SO101_SOURCE_PROJECT}`;
const TRANSFER_ROUTE = `/experiments/${TRANSFER_PROJECT}`;
const experimentIdentityExports = {ExplorerExperimentRegistry,experimentId,SO101_SOURCE_PROJECT,TRANSFER_PROJECT,SO101_SOURCE_ROUTE,TRANSFER_ROUTE};
Object.assign(typeof window !== "undefined" ? window : globalThis,experimentIdentityExports);
if (typeof module !== "undefined" && module.exports) module.exports = experimentIdentityExports;
'''.replace('REGISTRY_DOCUMENT',json.dumps(registry,ensure_ascii=False,separators=(',',':')))
    return {root/'_registry/projects.json':json.dumps(registry,ensure_ascii=False,indent=2)+'\n',
        root/'00-control-center/src/explorer/static/experiment-registry.js':browser}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    mismatches=[]
    for path,expected in generated_files(ROOT).items():
        if args.check:
            if not path.is_file() or path.read_text()!=expected:mismatches.append(str(path.relative_to(ROOT)))
        else:path.write_text(expected)
    if mismatches:
        print('Regenerate semantic experiment registry: '+', '.join(mismatches),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
