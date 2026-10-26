"""ENGINEERING_GLUE: select the measured offline native6 Diffusion interpreter.

No optional model library is imported in the control center. Paths are server
owned; neither a job nor a browser may select a Python executable. Runtime and
source fingerprints are pinned before dispatch and cannot change on resume.
"""
import json
from pathlib import Path
import subprocess

from explorer_shared.contracts import digest
from explorer_shared.learning.diffusion_backend import PROFILE_ID
from .qualification_bindings import sha,runtime_identity

ROOT=Path(__file__).resolve().parents[3]


def qualified_diffusion_runtime(release=None):
    root=Path(release or ROOT).resolve();proofs=root/'shared/training'
    binding_path=proofs/'DIFFUSION_NATIVE6_RUNTIME_BINDING.json'
    binding=json.loads(binding_path.read_bytes())
    if binding.get('schema')!='explorer_native6_diffusion_source_qualification_binding_v1':raise ValueError('Native6 Diffusion source qualification is unavailable')
    receipt_path=proofs/'DIFFUSION_NATIVE6_QUALIFICATION.json';receipt=json.loads(receipt_path.read_bytes())
    if sha(receipt_path)!=binding['qualification_result_sha256']:raise ValueError('Native6 Diffusion measured receipt checksum changed')
    if (not all(receipt.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) or receipt.get('backend')!='diffusion'
            or receipt.get('profile_id')!=PROFILE_ID or receipt.get('action_contract_id')!='so101_follower_calibrated_position6_v1'
            or receipt.get('optimizer_steps',0)<1 or not receipt.get('training_resume',{}).get('checkpoint_loaded')
            or receipt.get('physical_io') is not False):raise ValueError('Actual offline native6 optimizer/resume qualification is required')
    for name,expected in binding['implementation_sha256'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha(path)!=expected:raise ValueError('Diffusion source differs from the measured native6 implementation: '+name)
    executable=Path.home()/'.local/share/explorer/policy-runtimes/diffusion-0.38.0-cpu/bin/python'
    if str(executable)!=binding['actual_runtime_executable']:raise ValueError('Diffusion interpreter differs from the server-owned qualified runtime')
    runtime_path=executable.parent.parent/'explorer-runtime-binding.json'
    if sha(runtime_path)!=binding['runtime_binding_sha256']:raise ValueError('Isolated Diffusion runtime binding changed')
    isolated=json.loads(runtime_path.read_bytes())
    if isolated.get('schema')!='explorer_isolated_policy_runtime_binding_v1' or isolated.get('executable')!=str(executable) or isolated.get('prefix')!=str(executable.parent.parent):raise ValueError('Isolated Diffusion interpreter identity is invalid')
    for name,expected in isolated['configuration_files_sha256'].items():
        if not Path(name).resolve().is_relative_to(executable.parent.parent.resolve()) or sha(name)!=expected:raise ValueError('Diffusion venv/.pth configuration changed')
    implementation_path=proofs/'DIFFUSION_NATIVE6_IMPLEMENTATION_BINDING.json'
    implementation=json.loads(implementation_path.read_bytes())
    if (implementation.get('schema')!='explorer_native6_diffusion_implementation_binding_v1' or implementation.get('qualification_result_sha256')!=sha(receipt_path)
            or implementation.get('resume_signature')!=receipt['training_resume']['signature']):raise ValueError('Measured native6 implementation signature is unavailable')
    allowed=(executable.parent.parent.resolve(),Path(isolated['extends_read_only_base_site_packages']).resolve())
    for name,expected in implementation['official_implementation_sha256'].items():
        path=Path(name).resolve()
        if not any(path.is_relative_to(parent) for parent in allowed) or sha(path)!=expected:raise ValueError('Official measured Diffusion implementation changed')
    for name,expected in implementation.get('shared_implementation_sha256',{}).items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or sha(path)!=expected:raise ValueError('Shared measured Diffusion dependency changed')
    runtime=runtime_identity(executable)
    query="import importlib.metadata as m,json;from pathlib import Path;import hashlib;d=m.distribution('diffusers');p=Path(d._path);print(json.dumps({'version':d.version,'metadata_path':str(p/'METADATA'),'metadata_sha256':hashlib.sha256((p/'METADATA').read_bytes()).hexdigest(),'record_sha256':hashlib.sha256((p/'RECORD').read_bytes()).hexdigest()}))"
    process=subprocess.run([str(executable),'-c',query],capture_output=True,text=True,check=True,timeout=10)
    diffusion=json.loads(process.stdout)
    if diffusion.get('version')!='0.38.0':raise ValueError('Only the measured official diffusers0.38.0 runtime is accepted')
    runtime['distribution_records']['diffusers']=diffusion
    versions=dict(receipt.get('framework_versions',{}),lerobot='0.6.1')
    for name,expected in versions.items():
        if runtime['distribution_records'].get(name,{}).get('version')!=expected:raise ValueError('Installed policy runtime differs from actual native6 qualification: '+name)
    identity=dict(schema='explorer_native6_diffusion_dispatch_binding_v1',profile_id=PROFILE_ID,source_contract_id='so101_follower_calibrated_position6_v1',
        native6_qualification_sha256=sha(receipt_path),native6_implementation_binding_sha256=sha(implementation_path),pipeline_source_sha256=binding['implementation_sha256'],
        runtime_identity=runtime,physical_execution_authorized=False)
    return dict(executable=str(executable),binding_sha256=digest(identity),identity=identity)


def policy_backend_status():
    from explorer_shared.learning.registry import backend_catalogue
    result=backend_catalogue()
    try:
        selection=qualified_diffusion_runtime()
        readiness=dict(available=True,profile_id=PROFILE_ID,binding_sha256=selection['binding_sha256'],scope='offline_native6_source_only',physical_execution_authorized=False)
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        readiness=dict(available=False,profile_id=PROFILE_ID,reason=str(error),physical_execution_authorized=False)
    result['native6_diffusion_runtime']=readiness
    return result
