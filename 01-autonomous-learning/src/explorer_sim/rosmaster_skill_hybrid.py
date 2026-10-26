"""ENGINEERING_GLUE: compatibility alias; shared training is the authority.

Current consumers use explorer_shared.learning. Retire this shim after legacy
imports disappear, no earlier than 2026-11-09; immutable old releases are retained.
"""
import sys
from explorer_shared.learning import rosmaster_skill_hybrid as implementation
sys.modules[__name__] = implementation
