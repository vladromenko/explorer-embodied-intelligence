"""ENGINEERING_GLUE: one bounded actor/scope/JPEG delivery receipt cache."""
from collections import OrderedDict
import re
import threading
import time


class ObservedDisplayReceipts:
    def __init__(self,*,clock=time.monotonic,max_age_s=2.,capacity=256):
        self.clock,self.max_age_s,self.capacity=clock,max_age_s,capacity
        self.lock=threading.RLock();self.rows=OrderedDict()

    def record(self,kind,scope,identity,actor,checksum):
        if (any(not isinstance(value,str) or not 1<=len(value)<=256 for value in (kind,scope,identity,actor)) or
                not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum)):
            raise ValueError('Exact bounded source display scope/actor/JPEG SHA required')
        with self.lock:
            self.rows[(kind,scope,identity,actor)]=(checksum,self.clock())
            while len(self.rows)>self.capacity:self.rows.popitem(last=False)

    def consume(self,kind,scope,identity,actor,checksum):
        with self.lock:receipt=self.rows.pop((kind,scope,identity,actor),None)
        if receipt is None or receipt[0]!=checksum or not 0<=self.clock()-receipt[1]<self.max_age_s:
            raise ValueError('This exact source JPEG must first be delivered to this authenticated observer for this attempt; display receipt expired or consumed')
