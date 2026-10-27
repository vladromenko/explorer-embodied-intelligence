"""ENGINEERING_GLUE: official local LeRobotDataset interchange for native9 ACT.

Original canonical assets stay immutable. LeRobot's mandatory timestamp is a
logical frame-index grid, NOT camera exposure/native time; exact source clocks,
sample indices and pause segments remain separate columns and manifest facts.
The existing train-only normalizer and ACT preprocessing/math remain unchanged.
"""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from .rosmaster_skill_features import FEATURE_NAMES,PipelineBlocked,rgb_image
from .rosmaster_skill_segments import chunk_slots

SCHEMA='explorer_official_lerobot_native9_v1'
IMAGE_KEY='observation.images.overview'


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _json(path):
    if Path(path).stat().st_size>16*1024**2:raise PipelineBlocked('LEROBOT_INDEX_TOO_LARGE')
    return json.loads(Path(path).read_bytes())


def _rgb(corpus,episode,frame,image_size):
    value=rgb_image(frame['rgb_record'],lambda name:corpus.read(episode,name))
    return np.asarray(Image.fromarray(value).resize((image_size,image_size),Image.Resampling.BILINEAR))


def _segments(episode):
    groups=[]
    for frame in episode['frames']:
        if (not groups or groups[-1][-1]['recording_segment_id']!=frame['recording_segment_id'] or
                groups[-1][-1]['original_sample_index']+1!=frame['original_sample_index']):groups.append([])
        groups[-1].append(frame)
    return groups


def export_official_dataset(corpus,folder,*,image_size,partition,guard=lambda:None):
    """Worker-only local export. No Hub upload, resampling or source mutation."""
    if importlib.metadata.version('lerobot')!='0.6.1':raise PipelineBlocked('OFFICIAL_LEROBOT_DATASET_VERSION_NOT_QUALIFIED')
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    if partition not in ('train','development'):raise PipelineBlocked('LEROBOT_EXPORT_PARTITION_FORBIDDEN')
    selected=[episode for episode in corpus.episodes if episode['descriptor']['split']==partition]
    folder=Path(folder);index_path=folder/'explorer-source-index.json'
    if index_path.is_file():
        index=_json(index_path)
        if index.get('source_aggregate_sha256')!=corpus.sha256 or index.get('image_size')!=image_size or index.get('partition')!=partition or index.get('schema')!=SCHEMA:
            raise PipelineBlocked('LEROBOT_DERIVED_DATASET_IDENTITY_CHANGED')
        for name,expected in index['files_sha256'].items():
            file=(folder/name).resolve()
            if not file.is_relative_to(folder.resolve()) or _sha(file)!=expected:raise PipelineBlocked('LEROBOT_DERIVED_BYTES_CHANGED',name)
        return folder,index
    if folder.exists():raise PipelineBlocked('LEROBOT_INCOMPLETE_EXPORT_RETAINED',str(folder))
    folder.parent.mkdir(parents=True,exist_ok=True);stage=folder.with_name('.'+folder.name+'.'+uuid.uuid4().hex+'.exporting')
    features={IMAGE_KEY:{'dtype':'image','shape':(image_size,image_size,3),'names':['height','width','channels']},
        'observation.state':{'dtype':'float32','shape':(len(FEATURE_NAMES)*2,),
            'names':list(FEATURE_NAMES)+['available.'+name for name in FEATURE_NAMES]},
        'action':{'dtype':'float32','shape':(9,),'names':list(ACTION_NAMES)}}
    for key in ('source.sample_index','source.recording_segment_id','source.action_monotonic_ns','source.rgb_monotonic_ns'):
        features[key]={'dtype':'int64','shape':(1,),'names':None}
    guard();dataset=LeRobotDataset.create(repo_id='local/explorer_native9_'+corpus.sha256[:20]+'_'+partition,root=stage,fps=1,features=features,
        robot_type='rosmaster_unified_mobile_manipulator',use_videos=False,image_writer_processes=0,image_writer_threads=0)
    rows=[];episodes=[]
    try:
        for episode in selected:
            descriptor=episode['descriptor'];original=descriptor['source_episode_id']
            for segment in _segments(episode):
                exported=len(episodes);start=len(rows)
                for frame in segment:
                    guard();rgb=_rgb(corpus,episode,frame,image_size);sample=frame['sample'];state=np.concatenate([frame['values'],frame['masks'].astype(np.float32)]).astype(np.float32)
                    action_stamp=sample['action']['stamp'];rgb_stamp=sample['channels']['camera_rgb']['stamp']
                    dataset.add_frame({IMAGE_KEY:rgb,'observation.state':state,'action':frame['action'].astype(np.float32),
                        'source.sample_index':np.array([frame['original_sample_index']],np.int64),
                        'source.recording_segment_id':np.array([frame['recording_segment_id']],np.int64),
                        'source.action_monotonic_ns':np.array([action_stamp['monotonic_ns']],np.int64),
                        'source.rgb_monotonic_ns':np.array([rgb_stamp['monotonic_ns']],np.int64),
                        'task':corpus.spec['title']})
                    rows.append({'original_episode_id':original,'original_sample_index':frame['original_sample_index'],
                        'recording_segment_id':frame['recording_segment_id'],'partition':descriptor['split'],'lerobot_episode_index':exported,
                        'action_stamp':deepcopy(action_stamp),'rgb_stamp':deepcopy(rgb_stamp),
                        'resized_rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest()})
                dataset.save_episode(parallel_encoding=False)
                episodes.append({'lerobot_episode_index':exported,'original_episode_id':original,'partition':descriptor['split'],
                    'recording_segment_id':segment[0]['recording_segment_id'],'from_index':start,'to_index':len(rows)})
        dataset.finalize();guard();corpus.recheck()
        inventory={}
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                guard()
                with file.open('rb') as stream:os.fsync(stream.fileno())
                inventory[str(file.relative_to(stage))]=_sha(file)
        index={'schema':SCHEMA,'partition':partition,'source_aggregate_sha256':corpus.sha256,'skill_id':corpus.skill_id,'skill_version':corpus.version,
            'action_names':list(ACTION_NAMES),'action_units':list(ACTION_UNITS),'action_dimension':9,'state_dimension':len(FEATURE_NAMES)*2,
            'target_semantics':'actual_source_causal_issued9_not_preceding_command9','state_values':'raw_values_then_explicit_availability_masks',
            'image_size':image_size,'image_preprocessing':'source_RGB_PIL_bilinear_resize_once_then_lossless_storage',
            'lerobot_version':importlib.metadata.version('lerobot'),'lerobot_timestamp_basis':'logical_frame_index_grid_fps1_NOT_native_time_or_camera_exposure',
            'exact_source_clocks_retained':True,'temporal_resampling_performed':False,'interpolation_performed':False,
            'episodes':episodes,'rows':rows,'original_demo_count':len(selected),'segment_episode_count':len(episodes),
            'segments_do_not_inflate_demonstration_count':True,'sealed_payloads_exported':False,'source_kinds':sorted({row['report']['source_kind'] for row in selected}),
            'files_sha256':inventory}
        from .artifacts import write_json
        write_json(stage/'explorer-source-index.json',index)
        for directory in sorted([stage]+[path for path in stage.rglob('*') if path.is_dir()],key=lambda path:len(path.parts),reverse=True):
            descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        guard()
        os.replace(stage,folder)
        parent=os.open(folder.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
        return folder,index
    except BaseException:
        # Never replace the original cancellation/export error with cleanup.
        try:dataset.finalize()
        except Exception:pass
        raise


class OfficialFusedChunks:
    """Same discrete ACT tensors, sourced through official LeRobotDataset rows."""
    def __init__(self,corpus,episodes,chunk_size,image_size,folder,index):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        self.corpus,self.episodes,self.chunk_size,self.image_size=corpus,episodes,chunk_size,image_size
        self.folder,self.index=Path(folder),deepcopy(index)
        self.index_sha256=_sha(self.folder/'explorer-source-index.json')
        self.source_rgb_sha256={}
        self.dataset=LeRobotDataset(repo_id='local/explorer_native9_'+corpus.sha256[:20]+'_'+index['partition'],root=folder,download_videos=False)
        self.indices=[(i,j) for i,episode in enumerate(episodes) for j in range(len(episode['frames']))]
        self.lookup={(row['original_episode_id'],row['original_sample_index']):i for i,row in enumerate(index['rows'])}
        self.resume_context={'source_signature':corpus.sha256,'source_files':[{'path':path,'sha256':checksum} for path,checksum in sorted(corpus.source_files.items())]+
            [{'path':str(Path(folder)/name),'sha256':checksum} for name,checksum in sorted(index['files_sha256'].items())]+
            [{'path':str(Path(folder)/'explorer-source-index.json'),'sha256':_sha(Path(folder)/'explorer-source-index.json')}],
            'source_implementation_sha256':{name:_sha(Path(__file__).with_name(name)) for name in
                ('rosmaster_lerobot_dataset.py','rosmaster_skill_pipeline.py','rosmaster_skill_features.py','rosmaster_skill_hybrid.py','rosmaster_skill_segments.py')},
            'skill_id':corpus.skill_id,'skill_version':corpus.version,'feature_schema':corpus.normalizer.document(),
            'chunk_size':chunk_size,'image_size':image_size,'official_dataset_index_sha256':_sha(Path(folder)/'explorer-source-index.json')}
    def __len__(self):return len(self.indices)
    def recheck(self):
        if _sha(self.folder/'explorer-source-index.json')!=self.index_sha256:raise PipelineBlocked('LEROBOT_SOURCE_INDEX_CHANGED_DURING_TRAINING')
        for name,expected in self.index['files_sha256'].items():
            if _sha(self.folder/name)!=expected:raise PipelineBlocked('LEROBOT_SOURCE_CHANGED_DURING_TRAINING',name)
    def _item(self,episode,frame):
        key=(episode['descriptor']['source_episode_id'],frame['original_sample_index']);item=self.dataset[self.lookup[key]]
        state=item['observation.state'].numpy();action=item['action'].numpy()
        if (not np.array_equal(state[:len(FEATURE_NAMES)],frame['values']) or not np.array_equal(state[len(FEATURE_NAMES):],frame['masks'].astype(np.float32)) or
                not np.array_equal(action,frame['action']) or int(item['source.sample_index'].item())!=frame['original_sample_index'] or
                int(item['source.recording_segment_id'].item())!=frame['recording_segment_id'] or
                int(item['source.action_monotonic_ns'].item())!=frame['sample']['action']['stamp']['monotonic_ns'] or
                int(item['source.rgb_monotonic_ns'].item())!=frame['sample']['channels']['camera_rgb']['stamp']['monotonic_ns']):
            raise PipelineBlocked('LEROBOT_SOURCE_ROW_ROUNDTRIP_CHANGED')
        return item
    def __getitem__(self,index):
        import torch
        from .act_backend import prepare_rgb
        episode_index,position=self.indices[index];episode=self.episodes[episode_index];frame=episode['frames'][position]
        item=self._item(episode,frame);rgb=np.rint(item[IMAGE_KEY].numpy().transpose(1,2,0)*255).astype(np.uint8)
        expected=self.index['rows'][self.lookup[(episode['descriptor']['source_episode_id'],frame['original_sample_index'])]]
        source_key=(episode['descriptor']['source_episode_id'],frame['original_sample_index'])
        if source_key not in self.source_rgb_sha256:
            self.source_rgb_sha256[source_key]=hashlib.sha256(_rgb(self.corpus,episode,frame,self.image_size).tobytes()).hexdigest()
        actual_sha256=hashlib.sha256(rgb.tobytes()).hexdigest()
        if actual_sha256!=expected['resized_rgb_sha256'] or actual_sha256!=self.source_rgb_sha256[source_key]:raise PipelineBlocked('LEROBOT_RGB_ROUNDTRIP_CHANGED')
        slots,padded=chunk_slots(episode['frames'],position,self.chunk_size)
        actions=np.asarray([self._item(episode,episode['frames'][slot])['action'].numpy() for slot in slots])
        return {IMAGE_KEY:prepare_rgb(rgb[None])[0],
            'observation.state':torch.from_numpy(self.corpus.normalizer.state(frame['values'],frame['masks'])),
            'action':torch.from_numpy(self.corpus.normalizer.action(actions)),'action_is_pad':torch.tensor(padded,dtype=torch.bool)}
