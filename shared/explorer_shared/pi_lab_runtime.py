"""ENGINEERING_GLUE: expose existing03 metadata/data in the shared SO101 lab.

Manual user control reuses the original service; research qualification is separate.
No controller, camera, recorder, FK/IK or source loop is duplicated here.
"""


class LabRetargetRuntime:
    def __init__(self,service):
        self.service=service
        self.recorder=service.recorder

    @staticmethod
    def physical_admission():
        # Manual user control does not require a research qualification.
        # The original service still owns calibration, reference and leases.
        return dict(state='USER_CONTROL_AVAILABLE',allowed=True,reason='EXISTING_NATIVE_USER_CONTROL',
            evidence='03-lerobot-teaching/docs/CONTINUITY_20261027.json',
            physical_commands_authorized=False)

    def status(self):
        return {**self.service.status(),'runtime_profile':'so101_lab',
            'physical_path_admission':self.physical_admission(),
            'source04_capture_supported':self.service.station is not None,'physical03_commands_enabled':True}

    def command(self,operation,body):
        # Original command verifies ownership for scoped STOP/revoke. Data-only
        # capture/calibration/status retain their existing validation and owner.
        return self.service.command(operation,body)

    def start_worker(self):return self.service.start_worker()
    def stop(self,*args,**kwargs):
        result=self.service.stop(*args,**kwargs)
        receipt=result.get('last_stop_receipt') or {}
        return {**result,'native_STOP_delivered':bool(receipt.get('native_stop_attempted') and
            receipt.get('outcome')=='OWNED_STOP_API_RETURNED')}
    def close(self):return self.service.close()
