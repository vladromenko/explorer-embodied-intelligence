"""Shared training ownership/lazy-import boundaries, without robot or GPU access."""
import ast
import subprocess
import sys
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_shared_learning_never_imports_experiments_controlplane_or_hardware():
    forbidden=('explorer_sim','explorer.app','explorer.store','rclpy','serial','hailo_platform')
    for path in (ROOT/'shared/explorer_shared/learning').glob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            names=[alias.name for alias in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(name==base or name.startswith(base+'.') for name in names for base in forbidden),path


def test_controlplane_capabilities_resolve_without_any_experiment_path_or_torch():
    source="import sys;sys.path.insert(0,"+repr(str(ROOT/'shared'))+");from explorer_shared.learning.rosmaster_skill_pipeline import pipeline_capabilities;from explorer_shared.learning.registry import backend_catalogue;assert pipeline_capabilities()['fused_state_dimension']==106;assert backend_catalogue()['owner']=='shared_training_platform';assert not any(n=='torch' or n=='mujoco' or n.startswith('explorer_sim') for n in sys.modules)"
    process=subprocess.run([sys.executable,'-c',source],capture_output=True,text=True)
    assert process.returncode==0,process.stdout+process.stderr


def test_legacy_rosmaster_modules_are_same_objects_without_duplicate_implementation():
    from explorer_shared.learning import rosmaster_skill_pipeline as current
    from explorer_sim import rosmaster_skill_pipeline as legacy
    assert current is legacy
    assert Path(current.__file__).is_relative_to(ROOT/'shared')
