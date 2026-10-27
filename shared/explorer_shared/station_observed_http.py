"""ENGINEERING_GLUE: fixed authenticated metadata/native-boundary client.

Caller supplies a trusted configured-node request method, never a browser URL.
Instantiation issues no request; commands remain explicit USER-observed actions.
"""
from copy import deepcopy

from .station_observed_activation import binding,OPERATIONS


class StationObservedHttp:
    def __init__(self,request,selected):
        if not callable(request):raise ValueError('Trusted authenticated fixed-node request callback required')
        self.request,self.binding=request,binding(selected)
    def _same(self,value):
        if binding(value)!=self.binding:raise ValueError('Exact pinned native source/model binding required')
    def _post(self,operation,body):return self.request('POST','/station/observed-test/'+operation,body)
    def status(self):return self.request('GET','/station/observed-test/status',None)
    def challenge(self):return self._post('challenge',{'binding':deepcopy(self.binding)})
    def authorize(self,body,*,authenticated_actor):
        allowed=('challenge_id','displayed_jpeg_sha256','expected_control_epoch','observing','robot_workspace_checked','user_observed_test_confirmed')
        return self._post('authorize',{**{key:deepcopy(body[key]) for key in allowed if key in body},
            'binding':deepcopy(self.binding),'actor_id':authenticated_actor})
    def observe(self,grant_id,body,*,authenticated_actor):
        allowed=('frame_id','displayed_jpeg_sha256','expected_control_epoch','observing')
        return self._post('observe',{**{key:deepcopy(body[key]) for key in allowed if key in body},
            'grant_id':grant_id,'binding':deepcopy(self.binding),'actor_id':authenticated_actor})
    def check(self,grant_id,selected,expected_control_epoch=None):
        self._same(selected)
        return self._post('check',{'grant_id':grant_id,'binding':deepcopy(self.binding),'expected_control_epoch':expected_control_epoch})
    def command(self,grant_id,selected,operation,body):
        self._same(selected)
        if operation not in OPERATIONS:raise ValueError('Known original native station operation required')
        return self._post('command',{'grant_id':grant_id,'binding':deepcopy(self.binding),'operation':operation,'body':deepcopy(body)})
    def revoke(self,reason,*,expected_grant_id,expected_control_epoch,expected_binding=None):
        if expected_binding is not None:self._same(expected_binding)
        return self._post('revoke',{'reason':reason,'expected_grant_id':expected_grant_id,'expected_control_epoch':expected_control_epoch,
            'binding':deepcopy(self.binding)})

    def cooperative(self,operation,body=None,*,authenticated_actor=None):
        """Fixed metadata operation; source-native arrays never come from UI."""
        if operation=='status':
            if body not in (None,{}):raise ValueError('Fixed interlock status takes no body')
            return self.request('GET','/station/observed-test/cooperative/status',None)
        extra={'bind':{'direction_profile_id'},'arm':set(),'finish/challenge':{'giver_release_receipt'},
            'finish':{'challenge_id','confirmation'},'reset_safe':{'object_safe_no_dual_hold'},
            'successor':{'previous_grant_id','previous_control_epoch','observing','user_observed_test_confirmed'},
            'direction/observe':{'label','frame_id','displayed_jpeg_sha256','observing','empty_gripper_confirmed','user_observed_test_confirmed'}}
        common={'grant_id','expected_control_epoch','interlock_id','expected_revision'}
        if operation not in extra or not isinstance(body,dict) or set(body)-common-extra[operation] or not isinstance(authenticated_actor,str) or not authenticated_actor:
            raise ValueError('Typed fixed interlock USER operation with server actor required')
        return self._post('cooperative/'+operation,{**deepcopy(body),'actor_id':authenticated_actor})
