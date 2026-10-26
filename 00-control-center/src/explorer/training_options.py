"""ENGINEERING_GLUE: bounded backend options before any dataset/model allocation."""
from explorer_shared.learning.diffusion_backend import PROFILE_ID


def so101_training_settings(body):
    if not isinstance(body,dict):raise ValueError('Training options must be an object')
    backend=body.get('backend','act')
    if backend not in ('act','diffusion'):raise ValueError('Select ACT or the qualified native6 CPU Diffusion profile')
    if body.get('device','cpu')!='cpu':raise ValueError('SO101 source training uses explicitly qualified CPU profiles')
    if any(key.startswith('_') or key in ('source_numbering_version','numbering_provenance','executable','runtime') for key in body):raise ValueError('Execution provenance and runtime are assigned by the server')
    steps=body.get('optimizer_steps',body.get('act_steps',120))
    if 'optimizer_steps' in body and 'act_steps' in body and body['optimizer_steps']!=body['act_steps']:raise ValueError('Optimizer budget aliases disagree')
    batch=body.get('batch_size',2 if backend=='diffusion' else 4);image=body.get('image_size',64);seed=body.get('seed',0)
    values=(steps,batch,image,seed)
    if any(type(value) is not int for value in values) or not 1<=steps<=20000 or not 0<=seed<=1000000:raise ValueError('Bounded integer optimizer steps, batch, image size and seed required')
    if backend=='diffusion':
        if body.get('profile_id',PROFILE_ID)!=PROFILE_ID or image!=64 or not 1<=batch<=2 or 'chunk_size' in body:raise ValueError('Diffusion requires the exact native6 CPU profile, image64, batch<=2 and fixed horizon8/chunk4')
        return dict(backend=backend,profile_id=PROFILE_ID,optimizer_steps=steps,batch_size=batch,image_size=image,seed=seed,device='cpu')
    chunk=body.get('chunk_size',16)
    if not 32<=image<=160 or not 1<=batch<=16 or type(chunk) is not int or not 1<=chunk<=64 or 'profile_id' in body:raise ValueError('ACT settings exceed the bounded CPU workflow')
    return dict(backend=backend,act_steps=steps,batch_size=batch,image_size=image,chunk_size=chunk,seed=seed,device='cpu')
