"""ENGINEERING_GLUE: deprecated alias to the semantic transfer implementation."""
import sys
from . import transfer_mapping as _implementation
sys.modules[__name__]=_implementation
