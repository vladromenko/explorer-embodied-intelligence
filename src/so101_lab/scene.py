"""Real SAM3 on an immutable camera snapshot. No motor I/O, no fake live overlay."""
import json,time,uuid,os,threading
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from .jobs import atomic
from .resources import ROOT
from .annotations import SAM3Segmenter,sha256_file


def capture_scene(preview):
    folder=ROOT/'runtime/scenes'/uuid.uuid4().hex;folder.mkdir(parents=True)
    frame={'frame_id':folder.name,'timestamp_ns':time.monotonic_ns(),'cameras':{}}
    with preview.lock:
        if set(preview.cameras)!={'top','wrist'}:raise ValueError('Подключите обе камеры для проверки SAM')
        for camera,capture in preview.cameras.items():
            item=capture.latest_before(frame['timestamp_ns'])
            if item is None or frame['timestamp_ns']-item['timestamp_ns']>100_000_000:raise ValueError('Нет свежего кадра '+camera)
            file=folder/(camera+'.png');Image.fromarray(item['image']).save(file,format='PNG',compress_level=0)
            frame['cameras'][camera]={'timestamp_ns':item['timestamp_ns'],'valid':True,'path':file.name,'sha256':sha256_file(file)}
    atomic(folder/'request.json',{'purpose':'scene_preview_not_training_data','frame':frame,'captured_at':time.time()})
    return folder


def annotate_scene(folder,options,progress=None,stop=None):
    from .resources import ResourceLease
    with ResourceLease('gpu_compute' if options.get('device','cuda')=='cuda' else 'cpu_compute'):
        return _annotate_scene(folder,options,progress,stop)


def _annotate_scene(folder,options,progress=None,stop=None,segmenter=None):
    folder=Path(folder);request=json.loads((folder/'request.json').read_text());frame=request['frame']
    if progress:progress({'phase':'loading_model','message':'Загружаем настоящий SAM 3. Руки не открываются'})
    segmenter=segmenter or SAM3Segmenter(options).load()
    if stop and stop():return {'status':'stopped','physical_actions':False}
    images={}
    for camera,raw in frame['cameras'].items():
        file=folder/raw['path']
        if sha256_file(file)!=raw['sha256']:raise ValueError('Snapshot hash mismatch')
        with Image.open(file) as im:images[camera]=np.array(im.convert('RGB'))
    def mask_writer(camera,index,mask):
        file=folder/f'{camera}-mask-{index:04d}.png';Image.fromarray(np.asarray(mask,dtype=np.uint8)*255).save(file)
        return {'mask_path':file.name,'mask_sha256':sha256_file(file)}
    if progress:progress({'phase':'segmenting','message':'Определяем cube / box на сохранённом снимке двух камер'})
    annotation=segmenter.annotate_observation(frame,images,{},mask_writer)
    atomic(folder/'annotations.json',annotation)
    result={'status':'complete','scene_id':folder.name,'physical_actions':False,'not_live':True,
            'captured_at':request['captured_at'],'frame':frame,'images':{},'detected_objects':0,'ambiguous_objects':0}
    for camera,ann in annotation['cameras'].items():
        overlay=Image.fromarray(images[camera]).convert('RGBA')
        for obj in ann['objects']:
            tint=(255,177,64) if obj.get('category_ambiguous') else (42,205,152)
            mask=Image.open(folder/obj['mask_path']).convert('L').point(lambda p:int(p*.25))
            overlay=Image.alpha_composite(overlay,Image.composite(Image.new('RGBA',overlay.size,(*tint,255)),Image.new('RGBA',overlay.size,(0,0,0,0)),mask))
            draw=ImageDraw.Draw(overlay);draw.rectangle(obj['bbox_xyxy'],outline=(*tint,255),width=2)
            draw.text((max(0,obj['bbox_xyxy'][0]),max(0,obj['bbox_xyxy'][1]-14)),f"{obj['category']} {obj['score']:.2f}"+(' ?' if obj.get('category_ambiguous') else ''),fill=(*tint,255))
        file=folder/(camera+'-overlay.png');overlay.convert('RGB').save(file)
        result['images'][camera]={'overlay_url':f'/api/scenes/{folder.name}/{camera}/overlay','raw_url':f'/api/scenes/{folder.name}/{camera}/raw',
            'objects':ann['objects'],'timestamp_ns':ann['timestamp_ns'],'raw_sha256':ann['raw_sha256']}
        result['detected_objects']+=len(ann['objects'])
        result['ambiguous_objects']+=sum(bool(o.get('category_ambiguous')) for o in ann['objects'])
    atomic(folder/'result.json',result)
    return result


def watch_state(options):
    file=Path(options.get('recording_status_path',ROOT/'runtime/recording.json')).resolve()
    if not file.is_relative_to(ROOT):raise ValueError('Статус записи должен быть в собственной лаборатории')
    try:
        state=json.loads(file.read_text())
        return state if state.get('active') and state.get('session_id')==options['session_id'] else None
    except (OSError,ValueError):return None


def _last_committed_frame(path):
    with path.open('rb') as file:
        file.seek(0,2);size=file.tell();offset=max(0,size-65536);file.seek(offset)
        lines=file.read().splitlines()
    if offset:lines=lines[1:]
    for line in reversed(lines):
        try:return json.loads(line)
        except ValueError:pass  # The final line may still be in the writer's userspace buffer.
    return None


def watch_recording(dataset,options,progress=None,stop=None,publish=None):
    """Sparse, asynchronous preview only. Full training sidecars are a separate job."""
    from .resources import ResourceLease
    count=0;seen=None;latest=None;dataset=Path(dataset)
    with ResourceLease('gpu_compute'):
        if progress:progress({'phase':'loading_model','message':'SAM загружается отдельно от записи. Сырьё сохраняется с приоритетом'})
        segmenter=SAM3Segmenter(options).load()
        while not (stop and stop()):
            state=watch_state(options)
            if state is None:break
            episode=dataset/'.partial'/(str(state.get('episode_id'))+'.partial')
            try:
                frame=_last_committed_frame(episode/'frames.jsonl')
                identity=(state.get('episode_id'),frame['frame_id']) if frame else None
                if frame and identity!=seen:
                    folder=ROOT/'runtime/scenes'/uuid.uuid4().hex;folder.mkdir(parents=True)
                    for camera,raw in frame['cameras'].items():
                        original=(episode/raw['path']).resolve()
                        if not original.is_relative_to(episode.resolve()):raise ValueError('Raw path escapes episode')
                        target=folder/raw['path'];target.parent.mkdir(parents=True,exist_ok=True);os.link(original,target)
                    captured_at=time.time()-(time.monotonic_ns()-frame['timestamp_ns'])/1e9
                    atomic(folder/'request.json',{'purpose':'sparse_recording_preview_not_training_annotations','frame':frame,'captured_at':captured_at})
                    latest=_annotate_scene(folder,options,None,stop,segmenter)
                    if latest.get('images'):
                        count+=1;latest['sample_count']=count;latest['sparse_preview']=True;latest['source_episode_id']=identity[0]
                        if publish:publish(latest)
                        if progress:progress({'phase':'preview','sample_count':count,'detected_objects':latest['detected_objects'],
                            'message':'SAM: отдельный сохранённый кадр, не синхронный live; полный датасет размечается после STOP'})
                    seen=identity
            except (FileNotFoundError,OSError):pass  # Episode may atomically move at End/Re-record; retry reads only.
            threading.Event().wait(.25)
    return {**(latest or {}),'status':'complete','sample_count':count,'sparse_preview':True,'physical_actions':False}
