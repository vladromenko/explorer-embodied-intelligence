#!/usr/bin/env bash
set -euo pipefail
cd /home/jetsonnx/Projects/so101_ai_learning
if [ ! -d vendor/sam3 ]; then
  git clone https://github.com/facebookresearch/sam3.git vendor/sam3
  git -C vendor/sam3 checkout f1647e8d0c1d280991f4d1b7d974c2704537fe66
fi
python3 - <<'PY'
p='vendor/sam3/pyproject.toml'
s=open(p).read().replace('numpy>=1.26,<2','numpy>=2.0,<2.3')
open(p,'w').write(s)
PY
.venv/bin/python -m pip install --no-deps ./vendor/sam3
