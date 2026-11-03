#!/usr/bin/env bash
set -euo pipefail
cd /home/jetsonnx/Projects/so101_ai_learning
for unit in so101-ai-learning-ui so101-ai-learning-mlflow; do
  if systemctl --user is-active --quiet "$unit"; then continue; fi
  systemctl --user start "$unit"
done
