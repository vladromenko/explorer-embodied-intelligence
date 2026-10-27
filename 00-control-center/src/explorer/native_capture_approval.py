"""ENGINEERING_GLUE: compatibility API for the shared immutable Source review ABI.

The worker, canonical reader and control center use one pure validator. No model,
network or authority is constructed by importing this compatibility module.
"""
from explorer_shared.native_capture_approval import SCHEMA,source_bytes,validate_source_approval

__all__=['SCHEMA','source_bytes','validate_source_approval']
