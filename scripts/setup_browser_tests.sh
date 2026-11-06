#!/usr/bin/env bash
# Jetson Ubuntu 24.04 arm64: browsers/dependencies stay inside this repository.
set -euo pipefail
SO101_TEST_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SO101_TEST_ROOT"
export PLAYWRIGHT_BROWSERS_PATH="$SO101_TEST_ROOT/cache/playwright"
npm ci --cache "$SO101_TEST_ROOT/cache/npm"
node_modules/.bin/playwright install chromium webkit --only-shell
mkdir -p cache/browser-libs
(
    cd cache/browser-libs
    apt-get download libavif16=1.0.4-1ubuntu3 libgav1-1=0.18.0-1build3 'libyuv0=0.0~git202401110.af6ac82-1'
)
.venv/bin/python - <<'PY'
from pathlib import Path
import subprocess
root=Path.cwd()
for package in (root/'cache/browser-libs').glob('*.deb'):
    subprocess.run(['dpkg-deb','-x',str(package),str(root/'cache/browser-libs/extracted')],check=True)
libs=root/'cache/browser-libs/extracted/usr/lib/aarch64-linux-gnu'
for bundle in (root/'cache/playwright').glob('webkit-*'):
    for variant in ('minibrowser-wpe','minibrowser-gtk'):
        destination=bundle/variant/'sys/lib'
        if destination.is_dir():
            for name in ('libavif.so.16','libgav1.so.1','libyuv.so.0'):
                link=destination/name
                if not link.exists():link.symlink_to(libs/name)
PY
