"""Explicit generated paired05 inputs; reuse both existing producer/import factories.

All labels and frames are fabricated software inputs. This private Store must
never be used as physical evidence or imported into a production fleet.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import time
import uuid

from PIL import Image
from explorer.store import Store
from explorer import skill_workflow as mobile,lab_skill_workflow as lab
from explorer.cooperative_service import CooperativeService
from explorer_shared.cooperative_contract05 import PHASES
from explorer_shared.cooperative_recording05 import CooperativeRecorder
from test_skill_workflow import skill
from test_rosmaster_native02_e2e import native_aggregate
from test_so101_approved_capture_cohort import approved_cohort
from test_cooperative_service import Source

ACTOR='GENERATED_SOFTWARE_COHORT_NOT_A_PHYSICAL_OBSERVER'


def generated_pair_cohort(tmp_path,monkeypatch):
    tmp_path=Path(tmp_path);tmp_path.mkdir(parents=True,exist_ok=True)
    store=Store(tmp_path/'state');giver=skill(store)
    receiver=lab.propose(store,{'template_id':'pick_object_to_container'})
    receiver=lab.confirm(store,receiver['spec']['skill_id'],1,{'spec_sha256':receiver['spec_sha256']})
    root=CooperativeService({'nodes':[]},store);choices=root.native_choices()
    proposed=root.command('propose_skill',{'name':'GENERATED paired05 qualification','object_description':'SOFTWARE block',
        'receiver_goal_description':'SOFTWARE box','outcome_criteria':['GENERATED outcome, no physical claim'],
        'giver_choice_id':choices['giver'][0]['choice_id'],'receiver_choice_id':choices['receiver'][0]['choice_id'],
        **{role+'_phase_map':{phase:choices[role][0]['phase_ids'][0] for phase in PHASES} for role in ('giver','receiver')}},authenticated_actor=ACTOR)['result']
    spec=proposed['spec'];root.command('confirm_skill',{'skill_id':spec['skill_id'],'version':1,'spec_sha256':spec['spec_sha256']},authenticated_actor=ACTOR)
    variants={'giver':giver['spec']['coverage_plan']['variants'],'receiver':receiver['spec']['demo_plan']['variants']}
    rows=[];native_sessions={'giver':[],'receiver':[]}
    for index,(given,received) in enumerate(zip(variants['giver'],variants['receiver'],strict=True)):
        assert given['split']==received['split']
        row=root.command('create_session',{'skill_id':spec['skill_id'],'version':1,'spec_sha256':spec['spec_sha256'],
            'scene_id':'GENERATED_SHARED_SCENE_'+str(index),'object_instance_id':'GENERATED_SHARED_OBJECT_'+str(index),
            'split':given['split'],'native_variants':{'giver':given['variant_id'],'receiver':received['variant_id']},
            'giver_input_mode':'keyboard','receiver_input_mode':'leader','stationary_chassis_confirmed':True},authenticated_actor=ACTOR)['result']
        rows.append(row)
        native_id=str(uuid.UUID(hex=row['session_id']))
        for role,selected in (('giver',giver),('receiver',receiver)):
            create=mobile.create_session if role=='giver' else lab.create_session
            native_sessions[role].append(create(store,selected['spec']['skill_id'],1,
                {'session_id':native_id,'scene_id':row['scene_id'],'scene':row['scene_id'],'split':row['split'],'object_instance_id':row['object_instance_id']}))
    def context9(index,variant,session,context):
        row=rows[index];context['cooperative_parent']=deepcopy(row['proposed_native_source_contexts']['giver']['cooperative_parent'])
        context.update(cooperative_parent_session_id=row['session_id'],cooperative_source_execution_domain='REAL',
            qualification_input_basis='GENERATED_SOFTWARE_NO_PHYSICAL_INPUTS')
        context['split'].update(scene_id=row['scene_id'],object_instance_id=row['object_instance_id'],group_id=row['session_id'])
        return context
    def context6(index,variant,session,body):
        row=rows[index]
        body=lab.capture_context(store,receiver['spec']['skill_id'],1,{'variant_id':variant['variant_id'],
            'session_id':session['session_id'],'object_instance_id':row['object_instance_id']})['record_start_body']
        body['context'].update(cooperative_parent=deepcopy(row['proposed_native_source_contexts']['receiver']['cooperative_parent']),
            cooperative_parent_session_id=row['session_id'],cooperative_source_execution_domain='REAL',
            qualification_input_basis='GENERATED_SOFTWARE_NO_PHYSICAL_INPUTS')
        return body
    native_path,_,original9=native_aggregate(tmp_path/'giver',monkeypatch,store=store,selected=giver,
        session_for_variant=lambda index,variant:native_sessions['giver'][index],prepare_context=context9)
    _,_,original6=approved_cohort(tmp_path/'receiver',store=store,chosen=receiver,
        session_for_variant=lambda index,variant:native_sessions['receiver'][index],prepare_context=context6)
    pixel=BytesIO();Image.new('RGB',(32,24),(40,80,120)).save(pixel,format='JPEG');jpeg=pixel.getvalue()
    class CachedFixture(Source):
        def snapshot(self,cursor=0):return {**super().snapshot(cursor),'jpeg':jpeg}
    root.sources={role:CachedFixture(role) for role in ('giver','receiver')}
    root.recorder=CooperativeRecorder(root.root/'recordings',root.sources,clock_domain=root.domain)
    root.workflow.recorder=root.recorder
    reviewed=[]
    try:
        for row in rows:
            available=root._episode_choices(row)
            assert all(len(available[role])==1 for role in ('giver','receiver')),available
            references={role:{'recording_id':available[role][0]['original_episode_id']} for role in ('giver','receiver')}
            context={**row,'qualification_input_basis':'GENERATED_SOFTWARE_NO_PHYSICAL_INPUTS'}
            recording=root.recorder.start(context,references);identity=recording['recording_id']
            deadline=time.monotonic()+3
            while root.recorder.status()['sample_packets']==0 and time.monotonic()<deadline:time.sleep(.01)
            assert root.recorder.status()['sample_packets']>0
            for phase in PHASES:root.recorder.stage(phase,{'basis':'GENERATED explicit phase annotation, not a physical state'})
            root.recorder.finish({'status':'success','basis':'GENERATED operator label, no real success'})
            assert root.recorder.done.wait(3)
            # Source images remain original cached fixture bytes; registration
            # is a declared operator check, never metric calibration.
            root.command('register_zone',{'session_id':row['session_id'],'expected_epoch':row['epoch'],
                'both_robot_workspaces_checked':True,'observing':True},authenticated_actor=ACTOR)
            reply=root.command('review_recording',{'session_id':row['session_id'],'expected_epoch':row['epoch'],
                'recording_id':identity,'outcome':'success','whole_episode_seen':True,'object_instance_id':row['object_instance_id'],
                'phase_outcomes':{phase:'success' for phase in PHASES},
                'native_episode_choice_ids':{role:available[role][0]['choice_id'] for role in ('giver','receiver')}},authenticated_actor=ACTOR)['result']
            assert reply['admitted_for_training'],reply['reasons'];reviewed.append(reply)
        aggregate=root.command('aggregate',{'skill_id':spec['skill_id'],'version':1},authenticated_actor=ACTOR)['result']
        return root,store,spec,rows,reviewed,aggregate,{'native9_originals':original9,'source6_original_ids':[x[2]['episode_id'] for x in original6],
            'actual_physical_inputs':0,'native_source_kind_is_writer_declaration_not_evidence_of_hardware':True}
    except BaseException:root.close();raise
