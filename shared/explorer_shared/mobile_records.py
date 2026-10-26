"""ENGINEERING_GLUE: immutable copies of finished existing mobile recordings.

Reads the original recorder's files. No ROS, actuator, controller-setting or
source-recording writes. Archives live only in the new observer's state folder.
"""
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


class MobileRecords:
    def __init__(self,source,cache):
        self.source,self.cache=Path(source).resolve(),Path(cache)
        self.cache.mkdir(parents=True,exist_ok=True)

    def folder(self,ident):
        if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',ident):
            raise ValueError('Invalid mobile recording identity')
        folder=(self.source/ident).resolve()
        if folder.parent!=self.source:
            raise ValueError('Mobile recording escapes its source directory')
        return folder

    def descriptor(self,ident):
        data=json.loads((self.folder(ident)/'episode.json').read_text())
        if data.get('id')!=ident or data.get('format')!='explorer_mobile_episode_v3':
            raise ValueError('Existing recording identity or format differs')
        if data.get('state') not in ('complete','interrupted'):
            raise ValueError('Finish this mobile recording before transfer')
        return data

    def recordings(self):
        result=[]
        for path in sorted(self.source.glob('*/episode.json'),reverse=True):
            try:
                result.append(self.descriptor(path.parent.name))
            except (ValueError,OSError):
                pass
        return result

    @staticmethod
    def verified(path,files):
        try:
            with tarfile.open(path) as archive:
                for name,expected in files.items():
                    source=archive.extractfile(name)
                    if source is None or hashlib.file_digest(source,'sha256').hexdigest()!=expected:
                        return False
                transport=json.load(archive.extractfile('transport.json'))
                return transport['source_files_sha256']==files
        except (OSError,ValueError,KeyError,tarfile.TarError):
            return False

    def archive(self,ident):
        folder=self.folder(ident)
        self.descriptor(ident)
        with (self.cache/(ident+'.lock')).open('a') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX)
            names={'episode.json','samples.jsonl'}
            for line in (folder/'samples.jsonl').read_text().splitlines():
                if line.strip():
                    row=json.loads(line)
                    name=row.get('image')
                    if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:
                        raise ValueError('Invalid original mobile image path')
                    names.add(name)
            files={}
            for name in sorted(names):
                path=(folder/name).resolve()
                if not path.is_relative_to(folder) or not path.is_file():
                    raise ValueError('Original mobile source is missing or escapes its folder')
                files[name]=digest(path)
            fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
            target=self.cache/(ident+'-'+fingerprint+'.tar')
            if not target.exists() or not self.verified(target,files):
                temporary=target.with_suffix('.tmp')
                with tarfile.open(temporary,'w') as archive:
                    for name,expected in files.items():
                        path=(folder/name).resolve()
                        info=tarfile.TarInfo(name)
                        info.size=path.stat().st_size
                        with path.open('rb') as stream:
                            archive.addfile(info,stream)
                        if digest(path)!=expected:
                            raise ValueError('Original mobile source changed during transfer preparation')
                    transport=json.dumps({'source_files_sha256':files,'fingerprint':fingerprint,'original_retained':True},sort_keys=True).encode()
                    info=tarfile.TarInfo('transport.json');info.size=len(transport)
                    archive.addfile(info,io.BytesIO(transport))
                if not self.verified(temporary,files):
                    raise ValueError('Prepared mobile source archive checksum mismatch')
                temporary.replace(target)
            return target
