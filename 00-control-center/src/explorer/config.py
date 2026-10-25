"""ENGINEERING_GLUE: load private local runtime paths and connection settings."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load():
    path = Path(os.environ.get('EXPLORER_CONFIG', Path.home() / '.config/explorer/config.json'))
    if not path.is_file():
        raise RuntimeError('Explorer configuration is missing: run explorerctl init')
    result = json.loads(path.read_text())
    result['data_dir'] = str(Path(result['data_dir']).expanduser())
    result['root'] = str(ROOT)
    return result

