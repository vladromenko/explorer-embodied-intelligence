"""ENGINEERING_GLUE: canonical views over unchanged historical experiment bytes.

The migration index is bound to SQLite and SHA-checked. Views never replace the
raw event/card/config/result stored in SQLite or an immutable scientific file.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from explorer_shared.contracts import canonical,digest
from explorer_shared.experiments import canonical_experiment,experiment_slug,experiment_id

PROJECT_FIELDS={'project_id','experiment_id','owner_experiment','source_project_id','target_project_id','origin_project_id','canonical_project_id'}
PROJECT_ARRAYS={'compatible_source_projects','compatible_source_project_ids','experiment_ids'}
PROVENANCE_FIELDS={'legacy_provenance','original_spec','original_specs','numbering_provenance','original_record'}


def project_document(value,*,numbering_version):
    """A display projection with explicit original-vs-view hashes for SkillSpecs."""
    if isinstance(value,list):return [project_document(row,numbering_version=numbering_version) for row in value]
    if not isinstance(value,dict):return deepcopy(value)
    epoch=value.get('experiment_numbering_version',numbering_version)
    result={key:deepcopy(item) if key in PROVENANCE_FIELDS else project_document(item,numbering_version=epoch) for key,item in value.items()}
    changed={}
    for key in PROJECT_FIELDS & value.keys():
        original=value[key]
        if isinstance(original,str):
            try:current=canonical_experiment(original,numbering_version=epoch)
            except ValueError:current=original
            result[key]=current
            if current!=original:changed[key]=original
    for key in PROJECT_ARRAYS & value.keys():
        if isinstance(value[key],list):
            converted=[]
            for original in value[key]:
                try:converted.append(canonical_experiment(original,numbering_version=epoch))
                except ValueError:converted.append(original)
            result[key]=converted
            if converted!=value[key]:changed[key]=deepcopy(value[key])
    if changed:
        result['experiment_numbering_version']=2
        result['legacy_provenance']=dict(original_numbering_version=epoch,original_experiment_fields=changed,original_document_sha256=digest(value),raw_bytes_rewritten=False)
    if isinstance(value.get('spec'),dict) and result.get('spec')!=value['spec']:
        provenance=result.setdefault('legacy_provenance',{})
        provenance.update(original_spec=deepcopy(value['spec']),original_spec_sha256=digest(value['spec']),raw_bytes_rewritten=False)
        result['canonical_spec_sha256']=digest(result['spec'])
        result['spec_sha256_basis']='original_immutable_spec'
    return result


class ExperimentProjection:
    def __init__(self,root):
        self.path=Path(root)/'experiment-projection.json';self.signature=None;self.entries={};self.index_sha256=None

    def _load(self,db):
        marker=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_numbering_migrations'").fetchone()
        if marker is None:
            self.entries={};self.index_sha256=None;self.signature=None
            return 1
        row=db.execute('SELECT target_numbering_version,index_sha256 FROM experiment_numbering_migrations ORDER BY rowid DESC LIMIT 1').fetchone()
        if row is None:
            self.entries={};self.index_sha256=None;self.signature=None
            return 1
        if row[0]!=2:raise ValueError('Unsupported persisted experiment numbering migration')
        raw=self.path.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
        if checksum!=row[1]:raise ValueError('Experiment projection index differs from its SQLite binding')
        if checksum!=self.signature:
            body=json.loads(raw);entries=body.get('entries')
            if body.get('schema')!='explorer_experiment_projection_v1' or not isinstance(entries,list) or digest(entries)!=body.get('entries_sha256'):raise ValueError('Experiment projection index integrity failed')
            indexed={}
            for entry in entries:
                content={key:value for key,value in entry.items() if key!='projection_sha256'}
                if digest(content)!=entry.get('projection_sha256'):raise ValueError('Experiment projection entry integrity failed')
                key=(entry['entity_kind'],entry['entity_id'])
                if key in indexed and indexed[key]!=entry:raise ValueError('Conflicting experiment projection identity')
                indexed[key]=entry
            self.entries=indexed;self.signature=checksum;self.index_sha256=checksum
        return 2

    def identity(self,db,kind,identity,original,*,raw=None,declared_version=None):
        database_epoch=self._load(db);entry=self.entries.get((kind,identity))
        if entry:
            if raw is not None and kind in ('event','skill') and hashlib.sha256(raw).hexdigest()!=entry['raw_sha256']:raise ValueError('Projected immutable source body changed')
            return dict(project_id=entry['canonical_project_id'],experiment_slug=entry['experiment_slug'],experiment_numbering_version=2,
                legacy_experiment_id=entry['original_project_id'],original_numbering_version=entry['original_numbering_version'],
                numbering_provenance=dict(source='verified_migration_index',projection_sha256=entry['projection_sha256'],index_sha256=self.index_sha256,
                    original_project_id=entry['original_project_id'],original_numbering_version=entry['original_numbering_version'],deferred_active_pin=entry.get('deferred_active_pin',False)))
        known=db.execute('SELECT numbering_version,experiment_slug FROM experiment_runtime_identity WHERE entity_kind=? AND entity_id=?',(kind,identity)).fetchone()
        epoch=known[0] if known else declared_version if type(declared_version) is int and declared_version in (1,2) else database_epoch if kind=='job' else 1
        try:
            if known:
                slug=known[1];current=experiment_id(slug)
            else:
                current=canonical_experiment(original,numbering_version=epoch);slug=experiment_slug(original,numbering_version=epoch)
        except ValueError:return dict(project_id=original,experiment_numbering_version=epoch)
        result=dict(project_id=current,experiment_slug=slug,experiment_numbering_version=2,original_numbering_version=epoch)
        if current!=original:result.update(legacy_experiment_id=original,original_numbering_version=epoch)
        return result


def record_runtime_identity(db,kind,identity,project_id,*,numbering_version=2):
    try:slug=experiment_slug(project_id,numbering_version=numbering_version)
    except ValueError:return
    db.execute('INSERT OR IGNORE INTO experiment_runtime_identity VALUES (?,?,?,?)',(kind,identity,numbering_version,slug))
