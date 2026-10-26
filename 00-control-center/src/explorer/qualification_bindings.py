"""ENGINEERING_GLUE: immutable GPU qualification tied to the pinned job source.

No torch/CUDA imports or qualification claims are created here. A binding may
reuse measured evidence only when its recorded source hashes match every file
of the selected pipeline. Old bindings remain available after a new deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from email.parser import Parser


PIPELINES = {
    'sim_pilot': ('01-autonomous-learning/src/explorer_sim/pilot.py', '01-autonomous-learning/src/explorer_sim/student.py'),
    'train_act': ('01-autonomous-learning/src/explorer_sim/pilot.py','01-autonomous-learning/src/explorer_sim/student.py',
                  '01-autonomous-learning/src/explorer_sim/recorded_training.py'),
    'manipulation_student': tuple('01-autonomous-learning/src/'+name for name in (
        'explorer_sim/manipulation_student.py','explorer_sim/manipulation.py','explorer_sim/manipulation_env.py',
        'explorer_sim/recorded_training.py','explorer_sim/student.py','legacy/simulator/explorer_model.py',
        'legacy/simulator/environment.py','legacy/simulator/model_config.json','legacy/config/explorer.urdf','legacy/config/arm-motion.json')),
}
PACKAGES = ('torch','torchvision','lerobot','numpy','mujoco')


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def release_identity(release):
    release=Path(release).resolve()
    marker=release/'RELEASE_COMMIT'
    if not re.fullmatch('[0-9a-f]{40}',release.name) or not marker.is_file() or marker.read_text().strip()!=release.name:
        raise ValueError('Execution release is unavailable or is not commit-addressed')
    return release


def resolve_job_release(config,store,job):
    current=release_identity(config['root'])
    pinned=store.pin_job_release(job['id'],current.name)
    release=(current.parent/pinned).resolve()
    if release.parent!=current.parent or release.name!=pinned:
        raise ValueError('Pinned execution release identity changed or escapes its immutable directory')
    return release_identity(release)


def runtime_identity(executable):
    """Interpreter and installed distribution records, without loading GPU libs.

    This fingerprint does not claim to hash the OS/driver or every ELF dependency;
    the worker still checks actual CUDA availability and its qualified device.
    """
    executable=Path(executable).absolute()
    if not executable.is_file():raise ValueError('Qualified GPU interpreter is unavailable')
    venv=executable.parent.parent
    configuration=venv/'pyvenv.cfg'
    if not configuration.is_file():raise ValueError('GPU runtime must be an explicit versioned virtual environment')
    packages={}
    for site in sorted((venv/'lib').glob('python*/site-packages')):
        for metadata in site.glob('*.dist-info/METADATA'):
            fields=Parser().parsestr(metadata.read_text())
            name=re.sub('[-_.]+','-',str(fields.get('Name','')).lower())
            if name in PACKAGES:
                if name in packages:raise ValueError('Ambiguous GPU runtime distribution identity: '+name)
                record=metadata.parent/'RECORD'
                packages[name]={'version':fields.get('Version'),'metadata_sha256':sha(metadata),
                    'record_sha256':sha(record) if record.is_file() else None}
    if set(packages)!=set(PACKAGES):raise ValueError('GPU runtime distribution evidence is incomplete')
    return {'executable':str(executable),'resolved_executable_sha256':sha(executable.resolve()),
        'pyvenv_config_sha256':sha(configuration),'distribution_records':packages,
        'OS_and_GPU_driver_fully_hashed':False}


def pipeline_hashes(release,kind):
    root=release_identity(release)
    if kind not in PIPELINES:raise ValueError('This job has no qualified GPU pipeline contract')
    return {name:sha(root/name) for name in PIPELINES[kind]}


def _relative_sources(values):
    result={}
    for name,digest in values.items():
        parts=Path(name).parts
        if '01-autonomous-learning' not in parts:raise ValueError('Qualification source has no explicit monorepo module path')
        relative=Path(*parts[parts.index('01-autonomous-learning'):]).as_posix()
        if '..' in Path(relative).parts or not re.fullmatch('[0-9a-f]{64}',str(digest)):
            raise ValueError('Qualification source path/hash is invalid')
        if relative in result and result[relative]!=digest:raise ValueError('Ambiguous qualified source file')
        result[relative]=digest
    return result


def _qualified_sources(receipt,kind,proof=None):
    if (receipt.get('passed') is not True or type(receipt.get('optimizer_steps')) is not int
            or receipt['optimizer_steps']<1 or receipt.get('weights_changed') is not True):
        raise ValueError('Actual passed optimizer qualification evidence is required')
    qualified_kinds=receipt.get('qualified_job_kinds',[])
    if not isinstance(qualified_kinds,list) or any(not isinstance(value,str) for value in qualified_kinds):
        raise ValueError('Qualified pipeline kinds require an explicit list')
    if kind in qualified_kinds:
        sources=receipt.get('pipeline_source_sha256')
        if not isinstance(sources,dict):raise ValueError('Measured qualification has no pipeline source hashes')
        return _relative_sources(sources)
    # Existing actual nine-axis receipt has a hash-bound real training result
    # whose provenance records all ten implementation/configuration sources.
    if (kind=='manipulation_student' and receipt.get('state_dimension')==9 and receipt.get('action_dimension')==9
            and receipt.get('official_saved_processors_executed') is True and isinstance(proof,dict)):
        return _relative_sources(proof.get('provenance',{}).get('context',{}).get('source_sha256',{}))
    raise ValueError('This measured receipt does not qualify the selected source pipeline; backend-only is insufficient')


def _immutable(root,group,data):
    checksum=hashlib.sha256(data).hexdigest()
    folder=root/group;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(checksum+'.json')
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Immutable qualification evidence collision')
    else:
        with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return checksum


def publish_gpu_binding(qualification_path,release,kind,executable,registry_root):
    """Archive existing actual evidence; never manufacture a new qualification.

    Call explicitly after measurement, outside the scheduler. It is valid to bind
    another release with identical tested files; tested source hashes are kept.
    """
    release=release_identity(release);root=Path(registry_root)
    raw=Path(qualification_path).read_bytes();receipt=json.loads(raw)
    proof_raw=None;proof=None
    if receipt.get('result_path'):
        proof_raw=Path(receipt['result_path']).read_bytes()
        if hashlib.sha256(proof_raw).hexdigest()!=receipt.get('result_sha256'):
            raise ValueError('Actual qualification result checksum differs')
        proof=json.loads(proof_raw)
    expected=pipeline_hashes(release,kind);measured=_qualified_sources(receipt,kind,proof)
    if any(measured.get(name)!=digest for name,digest in expected.items()):
        raise ValueError('Pinned pipeline source differs from the actual measured qualification')
    runtime=runtime_identity(executable)
    dimensions=receipt.get('action_dimension')
    if type(dimensions) is not int or dimensions not in (6,9) or receipt.get('state_dimension')!=dimensions:
        raise ValueError('Actual qualification must explicitly record matching six- or nine-axis state/action dimensions')
    if kind in ('manipulation_student','sim_pilot') and dimensions!=9:
        raise ValueError('A six-axis receipt cannot qualify a nine-axis simulation pipeline')
    declared=dict(receipt.get('framework_versions') or {})
    if receipt.get('lerobot'):declared['lerobot']=receipt['lerobot']
    if proof:
        for name,version in proof.get('provenance',{}).get('context',{}).get('versions',{}).items():
            if name in declared and declared[name]!=version:raise ValueError('Qualification receipt and actual result disagree about runtime versions')
            declared[name]=version
    for name,version in declared.items():
        if name in runtime['distribution_records'] and runtime['distribution_records'][name]['version']!=version:
            raise ValueError('Installed GPU runtime differs from actual qualification: '+name)
    receipt_id=_immutable(root,'receipts',raw)
    proof_id=_immutable(root,'proofs',proof_raw) if proof_raw is not None else None
    body={'format':'explorer_gpu_source_binding_v1','execution_commit':release.name,'job_kind':kind,
        'pipeline_source_sha256':expected,'runtime_identity':runtime,'qualification_receipt_sha256':receipt_id,
        'qualification_result_sha256':proof_id,'scope':receipt.get('scope'),'physical_skill_qualified':False,
        'state_dimension':dimensions,'action_dimension':dimensions,
        'binding_basis':'recorded_measured_pipeline_hashes_match_execution_source_not_latest_checkout'}
    binding_id=_immutable(root,'bindings/'+release.name+'/'+kind,canonical(body))
    return {'binding_sha256':binding_id,'binding':body}


def qualified_gpu_binding(job,release,executable,registry_root,*,required_binding_sha256=None):
    release=release_identity(release);kind=job['kind'];root=Path(registry_root)
    expected=pipeline_hashes(release,kind);runtime=runtime_identity(executable)
    required_dimensions=9
    if kind=='train_act':
        manifest_path=job.get('config',{}).get('dataset_manifest')
        if not manifest_path:raise ValueError('GPU recorded training requires its explicit source action contract')
        manifest=json.loads(Path(manifest_path).read_text());names=manifest.get('action_names')
        if not isinstance(names,list) or len(names) not in (6,9):raise ValueError('GPU dataset has no qualified named action dimensions')
        required_dimensions=len(names)
    folder=root/'bindings'/release.name/kind
    errors=[]
    for path in sorted(folder.glob('*.json')):
        if required_binding_sha256 is not None and path.stem!=required_binding_sha256:
            pass
        else:
            try:
                raw=path.read_bytes();body=json.loads(raw)
                if hashlib.sha256(raw).hexdigest()!=path.stem:raise ValueError('Qualification binding is not immutable')
                if (body.get('format')!='explorer_gpu_source_binding_v1' or body.get('execution_commit')!=release.name
                        or body.get('job_kind')!=kind or body.get('pipeline_source_sha256')!=expected
                        or body.get('state_dimension')!=required_dimensions or body.get('action_dimension')!=required_dimensions
                        or body.get('runtime_identity')!=runtime):
                    raise ValueError('Qualification binding differs from pinned source or installed runtime')
                receipt_id=body['qualification_receipt_sha256'];receipt_path=root/'receipts'/(receipt_id+'.json')
                if sha(receipt_path)!=receipt_id:raise ValueError('Archived qualification receipt changed')
                receipt=json.loads(receipt_path.read_text());proof_id=body.get('qualification_result_sha256');proof=None
                if proof_id:
                    proof_path=root/'proofs'/(proof_id+'.json')
                    if sha(proof_path)!=proof_id or proof_id!=receipt.get('result_sha256'):raise ValueError('Archived actual result changed')
                    proof=json.loads(proof_path.read_text())
                measured=_qualified_sources(receipt,kind,proof)
                if any(measured.get(name)!=digest for name,digest in expected.items()):raise ValueError('Binding has no matching measured source evidence')
                return {'executable':str(Path(executable).absolute()),'binding_sha256':path.stem,
                    'binding_path':str(path),'execution_commit':release.name,'qualification_receipt_sha256':receipt_id}
            except (ValueError,KeyError,OSError) as error:errors.append(type(error).__name__+': '+str(error))
    raise ValueError('No immutable GPU qualification matches this pinned pipeline/runtime'+(': '+'; '.join(errors) if errors else ''))
