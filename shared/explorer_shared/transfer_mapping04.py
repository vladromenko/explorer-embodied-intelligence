"""Deprecated import alias; canonical transfer implementation has no numeric ID."""
import sys
from . import transfer_mapping as _implementation
sys.modules[__name__]=_implementation
