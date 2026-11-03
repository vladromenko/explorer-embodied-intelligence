"""Validated public training form. Importing this module never imports torch."""
from pathlib import Path
import hashlib,json,math,random
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class TrainingOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    policy_type:Literal['smolvla','act']='smolvla'
    mode:Literal['sam_objects','rgb_state']='sam_objects'
    camera_mode:Literal['two_camera','wrist_only_experimental']='two_camera'
    device:Literal['cuda','cpu']='cuda'
    steps:int=Field(default=1000,ge=1,le=1000000)
    batch_size:int=Field(default=1,ge=1,le=16)
    seed:int=Field(default=42,ge=0,le=2147483647)
    lr:float=Field(default=.0001,gt=0,le=.1)
    optimizer:Literal['adamw','adam']='adamw'
    weight_decay:float=Field(default=.0001,ge=0,le=1)
    grad_clip:float=Field(default=10,gt=0,le=1000)
    scheduler:Literal['cosine','constant']='cosine'
    warmup_steps:int=Field(default=0,ge=0,le=1000000)
    gradient_accumulation:int=Field(default=1,ge=1,le=64)
    use_amp:bool=False
    num_workers:int=Field(default=0,ge=0,le=2)
    log_every:int=Field(default=1,ge=1,le=1000000)
    save_every:int=Field(default=500,ge=1,le=1000000)
    eval_every:int=Field(default=100,ge=1,le=1000000)
    image_size:int=Field(default=256,ge=32,le=512)
    chunk_size:int=Field(default=50,ge=1,le=200)
    denoising_steps:int=Field(default=10,ge=1,le=100)
    kl_weight:float=Field(default=10,ge=0,le=1000)
    act_lr_backbone:float=Field(default=.00001,gt=0,le=.1)
    validation_fraction:float=Field(default=.2,gt=0,lt=1)
    test_fraction:float=Field(default=0,ge=0,lt=1)
    split_unit:Literal['auto','session','episode']='auto'
    split_mode:Literal['auto','manual']='auto'
    episodes:list[str]|None=None
    train_episodes:list[str]|None=None
    validation_episodes:list[str]|None=None
    test_episodes:list[str]|None=None
    annotation_version:str=Field(default='sam3-v1',pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    target_category:Literal['cube','box']='cube'
    target_color:str|None=None
    augment_images:bool=False
    job_name:str=Field(default='',max_length=120)
    resume_from:str|None=None
    @model_validator(mode='after')
    def coherent(self):
        if self.validation_fraction+self.test_fraction>=1:raise ValueError('Train доля должна быть больше нуля')
        if self.warmup_steps>=self.steps:raise ValueError('Warmup должен быть меньше steps')
        if self.image_size%16:raise ValueError('Размер изображения должен быть кратен16')
        if self.device=='cpu' and self.use_amp:raise ValueError('AMP поддерживается только CUDA')
        if self.split_mode=='manual' and (not self.train_episodes or not self.validation_episodes):raise ValueError('Вручную выберите train и validation эпизоды')
        if self.policy_type=='act' and self.camera_mode=='wrist_only_experimental' and self.image_size<64:raise ValueError('Для ACT выберите image_size ≥64')
        for name in ('episodes','train_episodes','validation_episodes','test_episodes'):
            values=getattr(self,name)
            if values is not None and (len(values)!=len(set(values)) or any(not x or Path(x).name!=x or x in ('.','..') for x in values)):raise ValueError('Некорректные/повторяющиеся episode IDs')
        return self

def normalize_options(options,*,public=False):
    options=dict(options)
    internal={k:options.pop(k) for k in ('tiny_model','allow_test_data','cpu_threads','base_revision','expected_split_sha256') if k in options}
    if public and internal:raise ValueError('Технические настройки запрещены в форме обычного обучения')
    # Tiny tests use 32px images and an abbreviated action horizon.
    if internal.get('tiny_model'):
        options.setdefault('image_size',32);options.setdefault('chunk_size',2);options.setdefault('device','cpu')
    result=TrainingOptions.model_validate(options).model_dump()
    if result['resume_from']:
        p=Path(result['resume_from']).resolve();root=Path(__file__).resolve().parents[2]
        if not p.is_relative_to(root/'models') and public:raise ValueError('Resume checkpoint должен быть внутри models лаборатории')
        if (p/'pretrained_model').is_dir():pass
        elif p.name=='pretrained_model':p=p.parent
        else:raise ValueError('Нужна полная папка checkpoint для resume')
        result['resume_from']=str(p)
    result.update(internal)
    return result

def plan_split(episodes,options):
    """Pure deterministic plan: same grouping used by preview and actual loop."""
    by_id={e['id']:e for e in episodes}
    if not by_id:raise ValueError('Нет завершённых эпизодов')
    selected=options.get('episodes')
    if selected is not None:
        missing=set(selected)-by_id.keys()
        if missing:raise ValueError('Эпизоды не найдены: '+', '.join(sorted(missing)))
        by_id={k:v for k,v in by_id.items() if k in selected}
    sessions={}
    for k,e in by_id.items():sessions.setdefault(e.get('session_id') or k,[]).append(k)
    unit=options.get('split_unit','auto')
    if unit=='auto':unit='session' if len(sessions)>1 else 'episode'
    if options.get('split_mode')=='manual':
        parts={k:list(options.get(k+'_episodes') or []) for k in ('train','validation','test')}
        assigned=sum(parts.values(),[])
        if len(set(assigned))!=len(assigned):raise ValueError('Эпизод не может входить в разные выборки')
        if set(assigned)-by_id.keys():raise ValueError('Выбраны неизвестные или исключённые эпизоды')
        if unit=='session':
            ownership={eid:k for k,ids in parts.items() for eid in ids}
            for ids in sessions.values():
                if len({ownership[e] for e in ids if e in ownership})>1:raise ValueError('Одна сессия разделена между выборками; выберите целые сессии')
    else:
        groups=[sorted(v) for _,v in sorted(sessions.items())] if unit=='session' else [[k] for k in sorted(by_id)]
        random.Random(int(options.get('seed',42))).shuffle(groups)
        n=len(groups);v=float(options.get('validation_fraction',.2));t=float(options.get('test_fraction',0))
        nv=max(1,math.floor(n*v+.5));nt=max(1,math.floor(n*t+.5)) if t else 0
        if n<nv+nt+1:raise ValueError(f'Недостаточно независимых {unit}: нужно минимум {nv+nt+1}, доступно {n}. Добавьте данные или измените split')
        parts={'validation':sum(groups[:nv],[]),'test':sum(groups[nv:nv+nt],[]),'train':sum(groups[nv+nt:],[])}
    if not parts['train'] or not parts['validation']:raise ValueError('Train и validation должны быть непустыми')
    result={'unit':unit,'mode':options.get('split_mode','auto'),'seed':int(options.get('seed',42)),**{k:sorted(v) for k,v in parts.items()},'excluded':sorted(by_id.keys()-set(sum(parts.values(),[]))),
            'warning':None if unit=='session' else 'Split по эпизодам: соседние эпизоды одной сессии могут быть похожи; для оценки обобщения нужны независимые сессии'}
    for k,ids in parts.items():
        result[k+'_samples']=sum(int(by_id[x].get('frames',0)) for x in ids)
        result[k+'_sessions']=sorted({by_id[x].get('session_id') or x for x in ids})
    result['sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result

def catalog(dataset_path):
    p=Path(dataset_path);meta=json.loads((p/'dataset.json').read_text());episodes=[]
    for ep in sorted((p/'episodes').glob('*/episode.json')):
        data=json.loads(ep.read_text())
        if data.get('status')!='complete':continue
        episodes.append({'id':ep.parent.name,'session_id':data.get('session_id') or ep.parent.name,'frames':data.get('frames',0),'source':data.get('source',meta.get('source')),'task':data.get('task',meta.get('task'))})
    return meta,episodes

def preview(dataset_path,options,*,public=True):
    opts=normalize_options(options,public=public);meta,episodes=catalog(dataset_path)
    if meta.get('source')!='hardware' and not opts.get('allow_test_data'):raise ValueError('Тестовый источник исключён из обычного обучения')
    plan=plan_split(episodes,opts)
    warnings=[plan['warning']] if plan['warning'] else []
    if opts['policy_type']=='act':warnings.append('ACT обучается с нуля, ResNet18 без скачивания весов; текст задачи не является входом ACT')
    if opts['mode']=='sam_objects':
        for eid in plan['train']+plan['validation']+plan['test']:
            sidecar=Path(dataset_path)/'episodes'/eid/'annotations'/opts['annotation_version']/'frames.jsonl'
            if not sidecar.is_file():raise ValueError(f'Нет SAM-аннотаций для {eid}; сначала завершите разметку или выберите RGB+state')
    if opts['policy_type']=='smolvla' and not opts.get('tiny_model'):
        from .training import cached_snapshot
        cached_snapshot('lerobot/smolvla_base');cached_snapshot('HuggingFaceTB/SmolVLM2-500M-Video-Instruct',require_weights=False)
    # Full raw/annotation validation is identical to the real worker and stays CPU-only.
    from .training import RawEpisodeDataset
    from .features import FeatureContract
    ds=RawEpisodeDataset(dataset_path,FeatureContract.from_dict(opts),chunk_size=opts['chunk_size'],allow_test_data=bool(opts.get('allow_test_data')),policy_type=opts['policy_type'],image_size=opts['image_size'],episode_ids=selected_episode_ids(opts))
    _,plan=ds.partitions(opts)
    if opts.get('resume_from'):
        checkpoint=Path(opts['resume_from']);folder=checkpoint/'pretrained_model'
        manifest=json.loads((checkpoint/'checkpoint.json').read_text());cfg=json.loads((folder/'config.json').read_text())
        if manifest.get('status')!='complete':raise ValueError('Resume checkpoint неполный')
        if public and manifest.get('technical_test'):raise ValueError('Технический checkpoint запрещён для обычного обучения')
        if int(manifest['step'])>=opts['steps']:raise ValueError('Resume steps должны быть больше шага checkpoint')
        if cfg.get('type')!=opts['policy_type']:raise ValueError('Тип модели в Resume checkpoint не совпадает с формой')
        if cfg.get('chunk_size')!=opts['chunk_size']:raise ValueError('Chunk size в Resume checkpoint отличается')
        saved=FeatureContract.from_dict(json.loads((folder/'object_adapter.json').read_text()))
        if saved.digest!=FeatureContract.from_dict(opts).digest:raise ValueError('Схема SAM/камер Resume отличается от формы')
        previous=json.loads((folder/'lineage.json').read_text())
        for part in ('train','validation','test'):
            if previous['split'].get(part,[])!=plan[part]:raise ValueError('Выборки Resume отличаются от исходных')
    if ds.rejections:warnings.append(f"Исключено невалидных observation frames: {len(ds.rejections)}")
    return {'options':opts,'split':plan,'episodes':episodes,'warnings':warnings,'source':meta.get('source'),'rejected_frames':len(ds.rejections)}


def selected_episode_ids(options):
    if options.get('split_mode')=='manual':
        return sum([options.get(k+'_episodes') or [] for k in ('train','validation','test')],[])
    return options.get('episodes')
