"""Shared immutable canonical dataset materialization, independent of experiment.

Original source bytes are copied and SHA-checked, never normalized or rewritten.
Callers own episode admission/splits; this writer does not infer expert quality.
"""
import os
from pathlib import Path
import shutil
import uuid

from .learning.artifacts import sha


def _write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


def _sync(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def write_immutable_bundle(destination,manifest_bytes,files):
    destination=Path(destination)
    if not isinstance(manifest_bytes,bytes):raise ValueError('Exact canonical manifest bytes required')
    for name in files:
        if (not isinstance(name,str) or not name or Path(name).is_absolute() or
                '\\' in name or any(part in ('','.','..') for part in name.split('/')) or name=='manifest.json'):
            raise ValueError('Safe unique canonical bundle payload path required')
    if destination.exists():
        if (destination/'manifest.json').read_bytes()!=manifest_bytes:raise ValueError('Immutable unified aggregate manifest changed')
        for name,(_,expected) in files.items():
            if sha(destination/name)!=expected:raise ValueError('Immutable unified aggregate payload changed')
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        stage=destination.with_name('.'+destination.name+'.'+uuid.uuid4().hex+'.staging');stage.mkdir()
        try:
            for name,(source,expected) in files.items():
                payload=source.read_bytes() if isinstance(source,Path) else source
                _write(stage/name,payload)
                if sha(stage/name)!=expected or (isinstance(source,Path) and sha(source)!=expected):
                    raise ValueError('Source changed while copying immutable canonical aggregate')
            _write(stage/'manifest.json',manifest_bytes)
            for directory,_,_ in os.walk(stage,topdown=False):_sync(directory)
            stage.rename(destination);_sync(destination.parent)
        except Exception:
            if stage.exists():shutil.rmtree(stage)
            raise
    return destination/'manifest.json'
