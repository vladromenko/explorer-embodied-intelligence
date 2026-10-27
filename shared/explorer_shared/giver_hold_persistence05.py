"""ENGINEERING_GLUE: metadata WAL for the existing giver publication latch.

No controller, publisher, camera or timer. Constructor/status are read-only.
Explicit metadata callers perform fsync; the publication path reads flags only.
"""
import json
import os
from pathlib import Path
import stat
import re
import uuid

from .contracts import canonical,digest

SCHEMA='explorer_native_giver_hold_wal_v1'
MARKER='explorer_native_giver_hold_initialized_v1'
LIMIT=1024*1024


def read(path,limit=LIMIT):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        meta=os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size>limit:raise ValueError('Bounded regular giver WAL required')
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('Giver WAL bound exceeded')
    return json.loads(raw)


def write(path,value,*,exclusive=False):
    raw=canonical(value)
    if len(raw)>LIMIT:raise ValueError('Giver WAL bound exceeded')
    temporary=path if exclusive else path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if not exclusive:os.replace(temporary,path)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:
        if not exclusive and temporary.exists():temporary.unlink()


class GiverHoldJournal:
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else None;self.state_id=uuid.uuid4().hex
        self.state=None;self.pending=False;self.fault=None;self.initialized=False;self.restored_armed=False;self.recovery_required=False
        self.last_sealed_sha256=None
        if self.path is not None:
            self.marker=self.path.with_name(self.path.name+'.initialized.json')
            try:
                if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError('Fixed source-owned giver metadata path required')
                if self.marker.exists() or self.marker.is_symlink():
                    self._marker()
                    if not self.path.exists():raise ValueError('Previously initialized giver WAL is missing')
                if self.path.exists():
                    if not self.marker.exists():raise ValueError('Initialized giver WAL lacks its source marker')
                    value=read(self.path)
                    if (not isinstance(value,dict) or value.get('schema')!=SCHEMA or
                            value.get('sha256')!=digest({key:item for key,item in value.items() if key!='sha256'}) or
                            not isinstance(value.get('state_id'),str) or not re.fullmatch('[0-9a-f]{32}',value['state_id']) or
                            not isinstance(value.get('state'),dict)):raise ValueError('Giver WAL schema/integrity differs')
                    self.state_id=value['state_id'];self.state=value.get('state');self.initialized=True
                    if value.get('pending') is not None or value.get('fault') is not None:raise ValueError('Pending/failed giver mutation requires USER safe-object recovery')
                    self.last_sealed_sha256=value['sha256']
            except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:self.fail('GIVER_WAL_UNCERTAIN:'+str(error))
    def fail(self,reason):
        self.fault=str(reason)[:1000];self.recovery_required=True
    def _marker(self,*,create=False,repair=False):
        expected={'schema':MARKER,'state_file':self.path.name,'marker_is_not_object_safety_or_authority':True}
        if self.marker.exists() or self.marker.is_symlink():
            try:
                if read(self.marker,4096)!=expected:raise ValueError('Giver initialized marker differs')
            except (ValueError,OSError):
                if not repair:raise
                write(self.marker,expected)
        elif create:write(self.marker,expected,exclusive=True)
        else:raise ValueError('Giver initialized marker missing')
    def _envelope(self,state,*,pending=None,fault=None):
        value={'schema':SCHEMA,'state_id':self.state_id,'state':state,'pending':pending,'fault':fault}
        return dict(value,sha256=digest(value))
    def begin(self,operation,state,*,recover=False):
        if self.path is None:return
        self.pending=True
        transaction={'operation':operation,'transaction_id':uuid.uuid4().hex,'before_state_sha256':digest(state)}
        try:
            if self.path.parent.is_symlink():raise ValueError('Source giver WAL directory changed')
            if recover:
                write(self.path,self._envelope(state,pending=transaction,fault=self.fault))
                self._marker(create=True,repair=True)
            else:
                self._marker(create=True)
                write(self.path,self._envelope(state,pending=transaction))
            self.initialized=True
        except Exception as error:self.fail('GIVER_WAL_BEGIN_FAILED:'+str(error));raise
    def seal(self,state,*,recover=False):
        if self.path is None:return
        value=self._envelope(state)
        try:write(self.path,value)
        except Exception as error:self.fail('GIVER_WAL_FINAL_SEAL_FAILED:'+str(error));raise
        self.state=state;self.last_sealed_sha256=value['sha256'];self.pending=False
        if recover:self.fault=None;self.recovery_required=False;self.restored_armed=False
    def status(self,revision):
        return {'installed':self.path is not None,'healthy':self.path is not None and self.fault is None and not self.pending,
            'initialized':self.initialized,'fail_closed_publication_gate':True,'state_id':self.state_id,'revision':revision,
            'recovery_required':self.recovery_required,'restored_armed':self.restored_armed,'fault':self.fault,'pending':self.pending,
            'last_sealed_sha256':self.last_sealed_sha256,'active_grants_or_pending_challenges_restored':False,
            'scope':'durable_armed_latch_not_restored_authorization_or_latest_published_pose'}
