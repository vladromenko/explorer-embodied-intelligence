"""Prepare the existing recording workflow behind the simple operator buttons.

Metadata only. No hardware call, grant or successful demonstration is created.
"""
import uuid
from . import skill_workflow as mobile
from . import lab_skill_workflow as source


def prepare(store,body):
    if not isinstance(body,dict) or set(body)-{'project_id','task','request_id','skill_id','skill_version'}:raise ValueError('Choose experiment and skill name')
    project=body.get('project_id');request=str(uuid.UUID(body.get('request_id')))
    title=body.get('task','').strip() or 'Ручной показ'
    if project not in ('02','03','04') or len(title)>100:raise ValueError('Experiment02–04 and a short skill name required')
    if project=='04':
        chosen=source._chosen(store,body['skill_id'],body['skill_version']) if body.get('skill_id') else source.propose(store,dict(template_id='pick_object_to_container',request_id=request))
        if chosen['spec']['title']!=title:
            chosen=source.revise(store,chosen['spec']['skill_id'],chosen['spec']['version'],dict(patch={'title':title},spec_sha256=chosen['spec_sha256']))
        chosen=source.confirm(store,chosen['spec']['skill_id'],chosen['spec']['version'],dict(spec_sha256=chosen['spec_sha256']))
        variant=next(row for row in chosen['spec']['demo_plan']['variants'] if row['split']=='train')
        session=source.create_session(store,chosen['spec']['skill_id'],chosen['spec']['version'],dict(scene_id='operator-current-scene',split='train'))
        context=source.capture_context(store,chosen['spec']['skill_id'],chosen['spec']['version'],dict(
            variant_id=variant['variant_id'],session_id=session['session_id'],object_instance_id='operator-current-object',name=title))
        return dict(project_id=project,context=context,phases=chosen['spec']['phase_ids'],skill=chosen)
    chosen=mobile.workflow(store,body['skill_id'],body['skill_version'],project_id=project) if body.get('skill_id') else mobile.create_skill(store,dict(project_id=project,preset='object_a_to_b',request_id=request))
    if chosen['spec']['title']!=title:
        chosen=mobile.revise_skill(store,chosen['spec']['skill_id'],chosen['spec']['version'],{'title':title},chosen['spec_sha256'])
    chosen=mobile.confirm_skill(store,chosen['spec']['skill_id'],chosen['spec']['version'],chosen['spec_sha256'])
    version=chosen['spec']['version'];skill=chosen['spec']['skill_id'];mode='so101_leader_retarget' if project=='03' else 'keyboard_gamepad'
    session=mobile.create_session(store,skill,version,dict(project_id=project,input_mode=mode,split='train',scene='operator-current-scene',object_instance_id='operator-current-object',group_id='operator-current-scene'))
    variant=next(row for row in chosen['spec']['coverage_plan']['variants'] if row['split']=='train')
    context=dict(skill_id=skill,version=version,spec_sha256=chosen['spec_sha256'],variant_id=variant['variant_id'],
        session_id=session['session_id'],project_id=project,input_mode=mode,request_id=request,
        episode_scope='whole_episode',target_phase_ids=[row['id'] for row in chosen['spec']['stages']],
        phase_id=chosen['spec']['stages'][0]['id'],stationary_chassis_confirmed=False)
    return dict(project_id=project,context=context,phases=context['target_phase_ids'],skill=chosen)
