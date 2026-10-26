"""Generic canonical artifact operations; no Torch, simulator or robot import."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as output:
        output.write(canonical(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def state_digest(model):
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()

