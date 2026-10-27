"""ENGINEERING_GLUE: exact, distinct Torch distribution/module build evidence.

Static preparation parses literal assignments from version.py; it never imports
or executes that file. Runtime comparison accepts no suffix normalization.
"""
import ast
from email.parser import BytesParser
from pathlib import Path

from .sim_train_forward_audit import require, file_sha, sha_bytes

SCHEMA = 'explorer_exact_torch_distribution_and_module_build_v1'


def pin_torch_build(version_file, metadata_file, distribution_version):
    version_file, metadata_file = Path(version_file).resolve(), Path(metadata_file).resolve()
    require(version_file.stat().st_size <= 65536 and metadata_file.stat().st_size <= 1024**2,
            'Bounded Torch identity bytes required')
    raw, metadata = version_file.read_bytes(), metadata_file.read_bytes()
    require(len(raw) <= 65536 and len(metadata) <= 1024**2, 'Bounded Torch identity bytes required')
    wanted = {'__version__', 'cuda', 'git_version'}
    tree = ast.parse(raw, filename=str(version_file))
    found = {}
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                require(node in tree.body and target.id not in found and isinstance(node.value, ast.Constant),
                        'Single top-level literal Torch version/build assignment required')
                value = node.value.value
                require(isinstance(value, str) and 0 < len(value) <= 128
                        or target.id == 'cuda' and value is None, 'Literal Torch build string (cuda may be None) required')
                found[target.id] = value
    require(set(found) == wanted, 'All exact module version/cuda/git literals required')
    require(len(found['git_version']) == 40 and all(c in '0123456789abcdef' for c in found['git_version']),
            'Exact installed Torch git commit literal required')
    headers = BytesParser().parsebytes(metadata)
    require(headers['Name'] == 'torch' and isinstance(distribution_version, str) and len(headers.get_all('Version', [])) == 1
            and headers['Version'] == distribution_version, 'Exact distinct distribution metadata version required')
    return dict(schema=SCHEMA, module_file=str(version_file), module_file_sha256=sha_bytes(raw),
                module_version=found['__version__'], cuda_build=found['cuda'], git_version=found['git_version'],
                metadata_file=str(metadata_file), metadata_file_sha256=sha_bytes(metadata),
                distribution_version=distribution_version, static_AST_only=True,
                suffix_normalization_performed=False, version_file_executed_in_preparation=False)


def check_torch_build(frozen, *, module_version, distribution_version, cuda_build, git_version, module_file):
    require(frozen.get('schema') == SCHEMA and isinstance(module_version, str), 'Reviewed Torch identity schema required')
    require(module_version == frozen['module_version'], 'Exact full Torch module build version differs')
    require(distribution_version == frozen['distribution_version'], 'Exact Torch distribution version differs')
    require(cuda_build == frozen['cuda_build'] and git_version == frozen['git_version'], 'Exact Torch cuda/git build literals differ')
    require(str(Path(module_file).resolve()) == frozen['module_file']
            and file_sha(module_file) == frozen['module_file_sha256'], 'Loaded Torch version.py path/bytes differ')
    require(file_sha(frozen['metadata_file']) == frozen['metadata_file_sha256'], 'Frozen Torch distribution metadata bytes differ')
    return True
