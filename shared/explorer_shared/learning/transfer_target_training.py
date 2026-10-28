"""06 training context: common ACT9 trainer, optional exact visual-backbone reuse.

No6D action/state/latent/optimizer head is transferred. New target heads and the
target TRAIN-only normalizer belong to native9. This is representation reuse,
not evidence of zero-shot transfer or full source/target task success.
"""
from copy import deepcopy
import json
from pathlib import Path

from explorer_shared.contracts import digest
from .artifacts import sha
from .rosmaster_skill_features import PipelineBlocked
from .transfer_target_corpus import SCHEMA, TransferTargetCorpus


class TransferTrainingContext:
    def __init__(self, *, source_model_binding, target_version, target_domain,
                 mode, source_checkpoint=None, source_checkpoint_sha256=None):
        if mode not in ('target_only', 'vision_adapt'):
            raise PipelineBlocked('EXPLICIT_TARGET_ONLY_OR_VISION_ADAPT_REQUIRED')
        self.binding = deepcopy(source_model_binding)
        self.version, self.domain, self.mode = target_version, target_domain, mode
        self.checkpoint = Path(source_checkpoint).resolve() if source_checkpoint else None
        self.hashes = deepcopy(source_checkpoint_sha256 or {})
        if mode == 'vision_adapt':
            self.recheck()

    def recheck(self):
        if self.mode == 'vision_adapt':
            if (not self.checkpoint or not self.checkpoint.is_dir() or not self.hashes or
                    any(Path(name).name != name for name in self.hashes) or
                    digest(self.hashes) != self.binding.get('checkpoint_content_sha256') or
                    {path.name for path in self.checkpoint.iterdir() if path.is_file()} != set(self.hashes)):
                raise PipelineBlocked('EXACT_SOURCE_CHECKPOINT_INVENTORY_REQUIRED')
            for name, expected in self.hashes.items():
                path = self.checkpoint/name
                if path.is_symlink() or not path.is_file() or sha(path) != expected:
                    raise PipelineBlocked('SOURCE_CHECKPOINT_CHANGED', name)

    def validate_corpus(self, corpus):
        adapter = TransferTargetCorpus(corpus.path, source_model_binding=self.binding,
            target_version=self.version, target_domain=self.domain)
        selection = corpus.manifest.get('transfer_target_selection') or {}
        if (selection.get('schema') != SCHEMA or selection.get('source_model_binding') != self.binding or
                selection.get('target_domain') != self.domain or selection.get('target_version') != self.version or
                selection.get('original_demonstration_budget') not in (5,10,25) or
                len(corpus.train) != selection['original_demonstration_budget'] or
                set(selection.get('training_original_episode_ids') or []) !=
                {row['descriptor']['source_episode_id'] for row in corpus.train} or
                set(selection.get('development_original_episode_ids') or []) !=
                {row['descriptor']['source_episode_id'] for row in corpus.development} or
                set(selection.get('sealed_original_episode_ids') or []) != set(corpus.sealed_ids)):
            raise PipelineBlocked('SERVER_BOUND_TARGET_SELECTION_REQUIRED')
        self.recheck()
        return adapter.readiness()

    def resume_context(self):
        self.recheck()
        return dict(schema='explorer_transfer_training_context_v1', mode=self.mode,
            source_model_binding=deepcopy(self.binding), target_version=self.version,
            target_domain=self.domain, source_checkpoint_sha256=deepcopy(self.hashes),
            implementation_sha256={name:sha(Path(__file__).with_name(name)) for name in
                ('transfer_target_training.py','transfer_target_corpus.py')})

    def initialize(self, policy):
        """Validate ALL names/shapes/bytes before one target tensor is changed."""
        import torch
        receipt = dict(self.resume_context(), source_action_head_copied=False,
            source_state_projection_copied=False, source_latent_transformer_copied=False,
            source_optimizer_copied=False, target_normalizer_fit_on='target_train_only',
            transferred_tensor_names=[], physical_execution_authorized=False,
            source_or_target_full_skill_inferred=False)
        if self.mode == 'target_only':
            return receipt
        from safetensors import safe_open
        config = json.loads((self.checkpoint/'config.json').read_bytes())
        preprocessing = json.loads((self.checkpoint/'explorer_preprocessing.json').read_bytes())
        if any(preprocessing.get(name) != expected for name, expected in dict(
                image_key='observation.images.overview', rgb_layout='NCHW', rgb_scale='uint8 / 255',
                mean=[.485,.456,.406],std=[.229,.224,.225]).items()):
            raise PipelineBlocked('SOURCE_TARGET_VISUAL_PREPROCESSING_MISMATCH')
        target_config = policy.config
        if (config.get('type') != 'act' or
                config.get('input_features', {}).get('observation.state', {}).get('shape') != [6] or
                config.get('output_features', {}).get('action', {}).get('shape') != [6] or
                config.get('vision_backbone') != target_config.vision_backbone or
                tuple(target_config.input_features['observation.state'].shape) != (106,) or
                tuple(target_config.output_features['action'].shape) != (9,)):
            raise PipelineBlocked('COMPATIBLE_SOURCE_ACT6_TO_NEW_TARGET_ACT9_REQUIRED')
        target = policy.state_dict()
        names = sorted(name for name in target if name.startswith('model.backbone.'))
        if not names:
            raise PipelineBlocked('EXPLICIT_VISUAL_BACKBONE_REQUIRED')
        with safe_open(self.checkpoint/'model.safetensors', framework='pt', device='cpu') as source:
            if sorted(name for name in source.keys() if name.startswith('model.backbone.')) != names:
                raise PipelineBlocked('SOURCE_TARGET_BACKBONE_NAMES_DIFFER')
            tensors = {name: source.get_tensor(name) for name in names}
        if any(tensors[name].shape != target[name].shape or tensors[name].dtype != target[name].dtype or
               (tensors[name].is_floating_point() and not torch.isfinite(tensors[name]).all()) for name in names):
            raise PipelineBlocked('SOURCE_TARGET_BACKBONE_SHAPE_DTYPE_OR_FINITE_MISMATCH')
        self.recheck()
        with torch.no_grad():
            for name in names:
                target[name].copy_(tensors[name].to(target[name].device))
        receipt.update(transferred_tensor_names=names, visual_backbone_only=True,
                       source_weights_sha256=self.hashes['model.safetensors'])
        return receipt
