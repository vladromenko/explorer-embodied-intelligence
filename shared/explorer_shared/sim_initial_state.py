"""ENGINEERING_GLUE: future stationary CAD trial initial-state capture/restore.

Only an injected live MuJoCo SIM provider is supported. No archived Python,
pickle, policy, camera/device or environment factory is executed. Initial-state
capture cannot retroactively complete an older trial or close its whole OS.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import inspect
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import sys

SCHEMA='explorer_stationary_sim_initial_state_v1'
PROFILE='explorer_manipulation_stationary_initial_v1'
MAX_ARRAY_BYTES=16*1024**2
MAX_PAYLOAD_BYTES=256*1024**2
ENV_STATE=('command','command_before','requested','raw_action','phase','steps','last_distance','record_substeps','substep_trace')
ENV_STATIC=('task','horizon','period','goal','half','rim','velocity','qids','dids','joint_q','servo_ids','pad_ids','object_geom_ids','object_id','support_ids')
ENV_ALLOWED=set(ENV_STATE+ENV_STATIC+('xml','model','data','evaluator','renderer','options','action_space','observation_space','_np_random','_np_random_seed'))
EVALUATOR_FIELDS={'initial_height','goal','half','rim','dt','grasped','lifted','carried','success','grasp_seconds','lift_seconds','settle_seconds','max_height_gain','substeps','contact_substeps','bilateral_substeps','last'}
PROVIDER_FIELDS={'mj','env','urdf_sha','phase_evidence_map','navigation_map_provider','mesh_files','model_sha','sequence','phase_results','boot','env_source','provider_identity'}
DERIVED_FIELDS=('xpos','xquat','xmat','xipos','ximat','geom_xpos','geom_xmat','site_xpos','site_xmat','flexvert_xpos','cvel','cacc','cinert','cdof','cdof_dot','subtree_com','actuator_length','actuator_velocity','sensordata','qacc')
OPTION_FIELDS=('label','frame','geomgroup','sitegroup','jointgroup','tendongroup','actuatorgroup','flexgroup','skingroup','flags','bvh_depth','flex_layer')


class InitialStateRejected(ValueError):pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()


def encode(value):
    """Bounded typed JSON; ndarray dtype/shape/bytes and tuple/set preserved."""
    import numpy as np
    if value is None or type(value) in (str,bool,int):return value
    if type(value) is float:
        if not math.isfinite(value):raise InitialStateRejected('Nonfinite profile value')
        return value
    if isinstance(value,(np.ndarray,np.generic)):
        array=np.asarray(value)
        if array.dtype.hasobject or array.dtype.kind not in 'biuf' or array.nbytes>MAX_ARRAY_BYTES or not np.isfinite(array).all():
            raise InitialStateRejected('Unsupported/oversized/nonfinite array state')
        return dict(_type='scalar' if isinstance(value,np.generic) else 'array',dtype=array.dtype.str,shape=list(array.shape),data=base64.b64encode(array.tobytes(order='C')).decode())
    if type(value) in (list,tuple,set):
        items=[encode(item) for item in value]
        if type(value) is set:items.sort(key=canonical)
        return dict(_type={list:'list',tuple:'tuple',set:'set'}[type(value)],items=items)
    if type(value) is dict:
        if any(type(key) is not str for key in value):raise InitialStateRejected('String-key profile mapping required')
        return dict(_type='dict',items={key:encode(item) for key,item in value.items()})
    raise InitialStateRejected('Unknown mutable profile value type: '+type(value).__name__)


def decode(value,depth=0):
    import numpy as np
    if depth>64:raise InitialStateRejected('Profile nesting exceeds bound')
    if value is None or type(value) in (str,bool,int):return value
    if type(value) is float:
        if not math.isfinite(value):raise InitialStateRejected('Nonfinite profile value')
        return value
    if not isinstance(value,dict):raise InitialStateRejected('Typed JSON profile value required')
    kind=value.get('_type')
    if kind in ('array','scalar'):
        try:
            dtype=np.dtype(value['dtype']);shape=value['shape']
            if dtype.hasobject or dtype.kind not in 'biuf' or not isinstance(shape,list) or len(shape)>8 or any(type(n) is not int or n<0 for n in shape):raise ValueError('array ABI')
            count=math.prod(shape);size=count*dtype.itemsize
            if size>MAX_ARRAY_BYTES or len(value['data'])>4*((MAX_ARRAY_BYTES+2)//3):raise ValueError('array byte bound')
            raw=base64.b64decode(value['data'],validate=True)
            if len(raw)!=size:raise ValueError('array byte count')
            result=np.frombuffer(raw,dtype=dtype).reshape(shape).copy()
            if not np.isfinite(result).all() or kind=='scalar' and shape:raise ValueError('scalar/finite contract')
            return result[()] if kind=='scalar' else result
        except (ValueError,TypeError,KeyError) as error:raise InitialStateRejected('Invalid typed array state') from error
    if kind=='dict':
        items=value.get('items')
        if not isinstance(items,dict) or any(type(key) is not str for key in items):raise InitialStateRejected('Typed mapping required')
        return {key:decode(item,depth+1) for key,item in items.items()}
    if kind in ('list','tuple','set'):
        if not isinstance(value.get('items'),list):raise InitialStateRejected('Typed sequence required')
        items=[decode(item,depth+1) for item in value['items']]
        return {'list':list,'tuple':tuple,'set':set}[kind](items)
    raise InitialStateRejected('Unknown typed JSON state tag')


@dataclass(frozen=True)
class InitialStateBundle:
    document:dict
    payloads:dict[str,bytes]
    @property
    def sha256(self):return digest(canonical(self.document))


def _read_native(raw,state_size):
    import numpy as np
    if type(state_size) is not int or not 0<state_size<=MAX_ARRAY_BYTES//8:raise InitialStateRejected('Bounded native state size required')
    stream=io.BytesIO(raw)
    try:
        version=np.lib.format.read_magic(stream)
        if version==(1,0):shape,fortran,dtype=np.lib.format.read_array_header_1_0(stream,max_header_size=10000)
        elif version==(2,0):shape,fortran,dtype=np.lib.format.read_array_header_2_0(stream,max_header_size=10000)
        else:raise InitialStateRejected('Unknown native NPY format')
    except (ValueError,EOFError) as error:raise InitialStateRejected('Invalid native NPY header') from error
    if shape!=(state_size,) or fortran or dtype!=np.dtype('float64') or len(raw)-stream.tell()!=state_size*8:
        raise InitialStateRejected('Native NPY ABI/bytes differ before allocation')
    result=np.frombuffer(raw,dtype=dtype,offset=stream.tell(),count=state_size).copy()
    if not np.isfinite(result).all():raise InitialStateRejected('Nonfinite native state')
    return result


def _member(name):
    if not isinstance(name,str) or not name or '\\' in name:raise InitialStateRejected('Portable snapshot member required')
    value=PurePosixPath(name)
    if value.is_absolute() or not value.parts or any(part in ('.','..') for part in value.parts) or str(value)!=name:raise InitialStateRejected('Snapshot member escapes its root')
    return name


def save_initial_bundle(bundle,folder):
    validate_bundle(bundle,bundle.sha256);folder=Path(folder)
    if folder.exists() or folder.is_symlink():raise InitialStateRejected('New immutable snapshot folder required')
    folder.mkdir(mode=0o700,parents=True)
    for name,raw in dict(bundle.payloads,**{'manifest.json':canonical(bundle.document)}).items():
        path=folder/_member(name);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with path.open('xb') as stream:os.chmod(path,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
    descriptor=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    return dict(manifest_sha256=bundle.sha256,files=len(bundle.payloads)+1)


def load_initial_bundle(folder,*,expected_sha256):
    folder=Path(folder);manifest=folder/'manifest.json'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>8*1024**2:raise InitialStateRejected('Bounded regular initial manifest required')
    raw=manifest.read_bytes()
    if digest(raw)!=expected_sha256:raise InitialStateRejected('Initial manifest SHA changed')
    document=json.loads(raw);payloads={};total=0
    if not isinstance(document.get('payloads'),dict) or not 0<len(document['payloads'])<=512:raise InitialStateRejected('Bounded initial payload inventory required')
    for name,entry in document['payloads'].items():
        path=folder/_member(name)
        if path.resolve()!=path.absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024**2:
            raise InitialStateRejected('Bounded nonaliased snapshot payload required')
        total+=path.stat().st_size
        if total>MAX_PAYLOAD_BYTES:raise InitialStateRejected('Initial payload inventory exceeds bound')
        payloads[name]=path.read_bytes()
    actual=[]
    for path in folder.rglob('*'):
        if path.is_symlink():raise InitialStateRejected('Snapshot links forbidden')
        if path.is_file():actual.append(path.relative_to(folder).as_posix())
    if set(actual)!=set(payloads)|{'manifest.json'}:raise InitialStateRejected('Unaccounted initial snapshot file')
    bundle=InitialStateBundle(document,payloads);validate_bundle(bundle,expected_sha256);return bundle


def _bytes(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024**2:raise InitialStateRejected('Bounded regular runtime/source bytes required')
    return path.read_bytes()


def _class_identity(value):
    cls=type(value);path=inspect.getsourcefile(cls)
    if not path:raise InitialStateRejected('Source-bound Python class required')
    return dict(module=cls.__module__,qualname=cls.__qualname__,sha256=digest(_bytes(path)))


def _profile(provider):
    import mujoco
    import numpy as np
    if getattr(provider,'physical_io',None) is not False or not isinstance(provider.env.model,mujoco.MjModel) or not isinstance(provider.env.data,mujoco.MjData):
        raise InitialStateRejected('Injected MuJoCo SIM provider required')
    env=provider.env
    if set(vars(env))-ENV_ALLOWED or set(vars(env.evaluator))!=EVALUATOR_FIELDS or set(vars(provider))!=PROVIDER_FIELDS:
        raise InitialStateRejected('Unknown mutable environment/evaluator/provider profile')
    if np.asarray(env.command).shape!=(6,) or provider.navigation_map_provider is not None or 'manipulation_base_fixture' not in env.xml:
        raise InitialStateRejected('Only explicit stationary six-servo/full-nine-neutral-base fixture supported')
    if env.steps!=0 or provider.sequence!=0 or provider.phase_results or env.substep_trace:
        raise InitialStateRejected('Capture/restore profile must be post-reset before first trial step')
    if env.model.nplugin or getattr(env.data,'threadpool',0):raise InitialStateRejected('Plugin/threadpool state has no qualified adapter')
    for name in ('control','passive','contactfilter','sensor','act_dyn','act_gain','act_bias'):
        getter=getattr(mujoco,'get_mjcb_'+name,None)
        if getter is not None and getter() is not None:raise InitialStateRejected('External MuJoCo callbacks are unsupported')
    return env


def _generator_state(owner):
    # Read __dict__ directly: .np_random would create an unused RNG lazily.
    generator=vars(owner).get('_np_random')
    return dict(attribute_present='_np_random' in vars(owner),state=None if generator is None else encode(generator.bit_generator.state))


def _restore_generator(owner,record):
    import numpy as np
    state=None if record['state'] is None else decode(record['state'])
    if state is None:
        if record['attribute_present']:owner.__dict__['_np_random']=None
        else:owner.__dict__.pop('_np_random',None)
    else:
        name=state.get('bit_generator')
        if name not in ('PCG64','PCG64DXSM','MT19937','Philox','SFC64'):raise InitialStateRejected('Unknown recorded NumPy bit generator')
        generator=np.random.Generator(getattr(np.random,name)(0));generator.bit_generator.state=state
        owner.__dict__['_np_random']=generator


def _model_bytes(model):
    import mujoco
    import numpy as np
    # Official3.17 API makes an independent copy with referenced assets embedded.
    embedded=mujoco.mj_embedAssets(model)
    size=mujoco.mj_sizeModel(embedded)
    if not 0<size<=64*1024**2:raise InitialStateRejected('Compiled MJB size exceeds profile bound')
    buffer=np.empty(size,dtype=np.uint8);mujoco.mj_saveModel(embedded,buffer=buffer)
    return buffer.tobytes()


def _runtime():
    import mujoco
    import numpy as np
    modules={}
    for name,module in sorted(sys.modules.items()):
        path=getattr(module,'__file__',None)
        if path and (name=='mujoco' or name.startswith('mujoco.') or name=='numpy' or name.startswith('numpy.')) and Path(path).suffix in ('.py','.so'):
            modules[name]=digest(_bytes(path))
    return dict(python=platform.python_version(),cache_tag=sys.implementation.cache_tag,byteorder=sys.byteorder,machine=platform.machine(),
        mujoco=mujoco.__version__,numpy=np.__version__,python_executable_sha256=digest(_bytes(Path(sys.executable).resolve())),modules_sha256=modules,
        libmujoco_sha256={path.name:digest(_bytes(path)) for path in Path(mujoco.__file__).parent.glob('libmujoco.so*')},
        numpy_libraries_sha256={path.name:digest(_bytes(path)) for path in (Path(np.__file__).parent.parent/'numpy.libs').glob('*') if path.is_file()},
        helper_sha256=digest(_bytes(Path(__file__))),
        scope='explicit_current_Python_NumPy_MuJoCo_module_bytes_not_full_OS_transitive_dependency_closure')


def _source_payloads(bindings):
    if not isinstance(bindings,dict) or not bindings or len(bindings)>128:raise InitialStateRejected('Explicit trusted source role bindings required')
    result={};identity={}
    for role,entry in bindings.items():
        if not isinstance(role,str) or not role or '/' in role or '\\' in role:raise InitialStateRejected('Bounded named source role required')
        raw=_bytes(entry['path']);checksum=digest(raw)
        if checksum!=entry['sha256']:raise InitialStateRejected('Trusted source bytes changed before capture/restore')
        name='source/'+checksum+'.bin';result[name]=raw;identity[role]=dict(sha256=checksum,file=name,bytes=len(raw))
    return identity,result


def capture_initial_trial(provider,*,scenario,source_bindings):
    """Capture the actual post-reset SIM instance; do not reset or observe it."""
    import mujoco
    import numpy as np
    env=_profile(provider);identity,payloads=_source_payloads(source_bindings)
    state=np.asarray(env.integration_state())
    if state.dtype!=np.dtype('float64') or state.ndim!=1 or not np.isfinite(state).all():raise InitialStateRejected('Exact finite float64 integration state required')
    stream=io.BytesIO();np.save(stream,state,allow_pickle=False);payloads['native-integration.npy']=stream.getvalue()
    payloads['compiled-model.mjb']=_model_bytes(env.model);payloads['model.xml']=env.xml.encode()
    assets={}
    for locator,expected in sorted(provider.mesh_files.items()):
        raw=_bytes(locator);checksum=digest(raw)
        if checksum!=expected:raise InitialStateRejected('Declared CAD asset bytes changed')
        name='assets/'+checksum+'.bin';payloads[name]=raw;assets[Path(locator).name]=dict(sha256=checksum,file=name,bytes=len(raw))
    python_random=sys.modules.get('random')
    state_doc=dict(environment=encode({name:getattr(env,name) for name in ENV_STATE}),static=encode({name:getattr(env,name) for name in ENV_STATIC}),
        evaluator=encode(vars(env.evaluator)),derived=encode({name:getattr(env.data,name).copy() for name in DERIVED_FIELDS}),
        warnings=encode([dict(number=int(item.number),lastinfo=int(item.lastinfo)) for item in env.data.warning]),
        options=encode({name:getattr(env.options,name) for name in OPTION_FIELDS}),
        env_rng=_generator_state(env),action_space_rng=_generator_state(env.action_space),observation_space_rng=_generator_state(env.observation_space),
        env_seed_attribute_present='_np_random_seed' in vars(env),env_seed=encode(vars(env).get('_np_random_seed')),
        numpy_legacy_rng=encode(np.random.get_state()),python_random_present=python_random is not None,
        python_rng=None if python_random is None else encode(python_random.getstate()),
        provider=encode(dict(sequence=provider.sequence,phase_results=provider.phase_results,phase_evidence_map=provider.phase_evidence_map)),
        renderer_initialized=env.renderer is not None,joint_q_aliases_qids=env.joint_q is env.qids)
    payloads['profile-state.json']=canonical(state_doc)
    if sum(map(len,payloads.values()))>MAX_PAYLOAD_BYTES:raise InitialStateRejected('Initial snapshot payload budget exceeded')
    document=dict(schema=SCHEMA,profile=PROFILE,scenario=encode(scenario),boundary='after_reset_before_first_observe_or_dispatch',
        environment_class=_class_identity(env),evaluator_class=_class_identity(env.evaluator),provider_class=_class_identity(provider),
        state_spec_name='mjSTATE_INTEGRATION',state_spec_value=int(mujoco.mjtState.mjSTATE_INTEGRATION),state_size=int(state.size),
        original_sim_clock_domain=provider.boot,source_bindings=identity,assets=assets,runtime=_runtime(),
        payloads={name:dict(sha256=digest(raw),bytes=len(raw)) for name,raw in sorted(payloads.items())},
        physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL',
        initial_RGB_render_reexecution='UNQUALIFIED',finite_command_reexecution='UNQUALIFIED_UNTIL_NEW_PID_TEST')
    return InitialStateBundle(document,payloads)


def validate_bundle(bundle,expected_sha256):
    if not isinstance(bundle,InitialStateBundle) or bundle.sha256!=expected_sha256 or bundle.document.get('schema')!=SCHEMA or bundle.document.get('profile')!=PROFILE:
        raise InitialStateRejected('Expected source-bound initial snapshot SHA/profile required')
    if not isinstance(bundle.payloads,dict) or not 0<len(bundle.payloads)<=512:raise InitialStateRejected('Bounded initial payload inventory required')
    if set(bundle.payloads)!=set(bundle.document['payloads']) or sum(map(len,bundle.payloads.values()))>MAX_PAYLOAD_BYTES:
        raise InitialStateRejected('Initial snapshot payload inventory changed')
    for name,raw in bundle.payloads.items():
        _member(name)
        if name=='manifest.json':raise InitialStateRejected('Reserved snapshot member')
        if type(raw) is not bytes or len(raw)!=bundle.document['payloads'][name]['bytes'] or digest(raw)!=bundle.document['payloads'][name]['sha256']:
            raise InitialStateRejected('Initial snapshot payload bytes changed')
    if bundle.document.get('physical_io') is not False or bundle.document.get('retroactive_capture') is not False or bundle.document.get('original_runtime_OS_closure')!='PARTIAL':
        raise InitialStateRejected('Initial snapshot cannot authorize hardware or complete original OS closure')
    return bundle.document


def restore_initial_trial(provider,bundle,*,expected_sha256,expected_source_bindings):
    """Validate all bound bytes/runtime BEFORE applying any initial SIM state."""
    import mujoco
    import numpy as np
    doc=validate_bundle(bundle,expected_sha256);env=_profile(provider)
    identity,unused=_source_payloads(expected_source_bindings)
    if identity!=doc['source_bindings'] or _runtime()!=doc['runtime']:
        raise InitialStateRejected('Initial snapshot source/runtime differs before restore')
    if _class_identity(env)!=doc['environment_class'] or _class_identity(env.evaluator)!=doc['evaluator_class'] or _class_identity(provider)!=doc['provider_class']:
        raise InitialStateRejected('Initial snapshot Python implementation differs')
    if digest(_model_bytes(env.model))!=doc['payloads']['compiled-model.mjb']['sha256'] or digest(env.xml.encode())!=doc['payloads']['model.xml']['sha256']:
        raise InitialStateRejected('Initial snapshot compiled model/XML differs')
    current_assets={Path(path).name:expected for path,expected in provider.mesh_files.items()}
    if current_assets!={name:row['sha256'] for name,row in doc['assets'].items()} or any(digest(_bytes(path))!=expected for path,expected in provider.mesh_files.items()):
        raise InitialStateRejected('Initial snapshot actual CAD assets differ')
    profile=json.loads(bundle.payloads['profile-state.json'])
    if canonical(encode({name:getattr(env,name) for name in ENV_STATIC}))!=canonical(profile['static']):
        raise InitialStateRejected('Initial snapshot environment configuration differs')
    decoded={name:decode(profile[name]) for name in ('environment','evaluator','derived','warnings','options','provider')}
    if set(decoded['environment'])!=set(ENV_STATE) or set(decoded['evaluator'])!=EVALUATOR_FIELDS or set(decoded['derived'])!=set(DERIVED_FIELDS) or set(decoded['options'])!=set(OPTION_FIELDS):
        raise InitialStateRejected('Initial snapshot mutable field inventory differs')
    if decoded['environment']['steps']!=0 or decoded['environment']['substep_trace'] or decoded['provider']['sequence']!=0 or decoded['provider']['phase_results'] or decoded['provider']['phase_evidence_map']!=provider.phase_evidence_map:
        raise InitialStateRejected('Initial snapshot phase/reset boundary differs')
    for name,array in decoded['derived'].items():
        current=np.asarray(getattr(env.data,name))
        if not isinstance(array,np.ndarray) or array.shape!=current.shape or array.dtype!=current.dtype:
            raise InitialStateRejected('Derived native array ABI differs')
    for name,value in decoded['options'].items():
        current=getattr(env.options,name)
        if isinstance(current,np.ndarray) and (not isinstance(value,np.ndarray) or value.shape!=current.shape or value.dtype!=current.dtype):
            raise InitialStateRejected('Visualization option ABI differs')
    if len(decoded['warnings'])!=len(env.data.warning):raise InitialStateRejected('Native warning inventory differs')
    native=_read_native(bundle.payloads['native-integration.npy'],doc['state_size'])
    if native.dtype!=np.dtype('float64') or native.shape!=(doc['state_size'],) or doc['state_spec_value']!=int(mujoco.mjtState.mjSTATE_INTEGRATION) or len(native)!=mujoco.mj_stateSize(env.model,mujoco.mjtState.mjSTATE_INTEGRATION) or not np.isfinite(native).all():
        raise InitialStateRejected('Initial snapshot native integration ABI differs')
    python_state=None if profile['python_rng'] is None else decode(profile['python_rng'])
    if profile['python_random_present'] and sys.modules.get('random') is None:raise InitialStateRejected('Recorded Python RNG module is unavailable')
    if profile['python_random_present']:
        probe=sys.modules['random'].Random(0);probe.setstate(python_state)
    legacy_state=decode(profile['numpy_legacy_rng']);probe=np.random.RandomState(0);probe.set_state(legacy_state)
    # Validate each recorded generator on a temporary owner BEFORE mutating the
    # actual source-bound environment. No previously unused owner is sampled.
    class RNGProbe:pass
    for name in ('env_rng','action_space_rng','observation_space_rng'):_restore_generator(RNGProbe(),profile[name])
    original_clock=doc['original_sim_clock_domain'];replay_clock=provider.boot
    if not isinstance(replay_clock,str) or replay_clock==original_clock:raise InitialStateRejected('New replay must have its own SIM clock identity')
    # Derived arrays match the actual recorded pre-observation buffers; forward
    # refreshes the remaining workspace, then native warmstart is re-applied.
    mujoco.mj_setState(env.model,env.data,native,mujoco.mjtState.mjSTATE_INTEGRATION);mujoco.mj_forward(env.model,env.data)
    mujoco.mj_setState(env.model,env.data,native,mujoco.mjtState.mjSTATE_INTEGRATION)
    for name,value in decoded['derived'].items():getattr(env.data,name)[:]=value
    for item,record in zip(env.data.warning,decoded['warnings'],strict=True):item.number=record['number'];item.lastinfo=record['lastinfo']
    for name,value in decoded['environment'].items():setattr(env,name,value)
    for name,value in decoded['evaluator'].items():setattr(env.evaluator,name,value)
    if profile['joint_q_aliases_qids']:env.joint_q=env.qids
    for name,value in decoded['options'].items():
        current=getattr(env.options,name)
        if isinstance(current,np.ndarray):current[:]=value
        else:setattr(env.options,name,value)
    provider_state=decoded['provider'];provider.sequence=provider_state['sequence'];provider.phase_results=provider_state['phase_results']
    _restore_generator(env,profile['env_rng']);_restore_generator(env.action_space,profile['action_space_rng']);_restore_generator(env.observation_space,profile['observation_space_rng'])
    if profile['env_seed_attribute_present']:env.__dict__['_np_random_seed']=decode(profile['env_seed'])
    else:env.__dict__.pop('_np_random_seed',None)
    np.random.set_state(legacy_state)
    if profile['python_random_present']:sys.modules['random'].setstate(python_state)
    if not np.array_equal(env.integration_state(),native):raise InitialStateRejected('Native state differs immediately after restore')
    return dict(schema='explorer_stationary_initial_restore_receipt_v1',initial_snapshot_sha256=expected_sha256,
        original_sim_clock_domain=original_clock,replay_sim_clock_domain=replay_clock,new_clock_identity=True,
        native_initial_state_equal=True,recorded_profile_derived_buffers_restored=True,
        original_runtime_OS_closure='PARTIAL',initial_RGB_render_reexecution='UNQUALIFIED',physical_io=False)
