"""Thin06 target-data admission over the shared canonical native9 reader.

No import/recorder/trainer duplication and no source6 action/image conversion.
The caller supplies a server-bound source/wizard, never browser measurement JSON.
Selection counts original whole target demonstrations, retaining DEV and sealed
identities. This software contract does not qualify a source or physical skill.
"""
from copy import deepcopy
import hashlib
import fcntl
from pathlib import Path
import uuid

import numpy as np

from explorer_shared.contracts import canonical, digest
from .rosmaster_skill_pipeline import CanonicalCorpus
from .rosmaster_skill_features import PipelineBlocked

SCHEMA = 'explorer_transfer_target_selection_v1'
BUDGETS = (5, 10, 25)


class TransferTargetCorpus:
    def __init__(self, manifest_path, *, source_model_binding, target_version,
                 target_domain, source_rgb_pixel_sha256=()):
        binding = deepcopy(source_model_binding)
        try:
            valid_uuid = str(uuid.UUID(binding['skill_id'])) == binding['skill_id']
        except (ValueError, TypeError, KeyError, AttributeError):
            valid_uuid = False
        hashes = ('spec_sha256', 'aggregate_manifest_sha256', 'training_receipt_sha256',
                  'checkpoint_content_sha256', 'source_calibration_sha256', 'source_urdf_sha256')
        if (not valid_uuid or not binding.get('model_job_id') or
                any(not isinstance(binding.get(key), str) or len(binding[key]) != 64 or
                    any(c not in '0123456789abcdef' for c in binding[key]) for key in hashes)):
            raise PipelineBlocked('ADMITTED_SOURCE_MODEL_BINDING_REQUIRED')
        if (type(binding.get('skill_version')) is not int or binding['skill_version'] < 1 or
                type(target_version) is not int or target_version <= binding['skill_version']):
            raise PipelineBlocked('SAME_SKILL_ADVANCING_TARGET_VERSION_REQUIRED')
        if target_domain not in ('simulation', 'physical'):
            raise PipelineBlocked('EXPLICIT_TARGET_DOMAIN_REQUIRED')
        corpus = CanonicalCorpus(manifest_path)
        if corpus.skill_id != binding['skill_id'] or corpus.version != target_version:
            raise PipelineBlocked('TARGET_SKILL_UUID_OR_VERSION_MISMATCH')
        source_pixels = set(source_rgb_pixel_sha256)
        if any(not isinstance(value, str) or len(value) != 64 for value in source_pixels):
            raise PipelineBlocked('SOURCE_RGB_PIXEL_INVENTORY_INVALID')
        for episode in corpus.episodes:
            report, descriptor = episode['report'], episode['descriptor']
            identity = report.get('experiment_identity') or {}
            if (identity.get('canonical_project_id') != '06' or
                    descriptor.get('source_project_id') != '06'):
                raise PipelineBlocked('ORIGINAL_TARGET06_IDENTITY_REQUIRED')
            if report['source_kind'] != target_domain:
                raise PipelineBlocked('TARGET_DOMAIN_MIXING_FORBIDDEN')
            if descriptor.get('episode_scope') != 'whole_episode':
                raise PipelineBlocked('WHOLE_ORIGINAL_TARGET_DEMONSTRATIONS_REQUIRED')
            for frame in episode['frames']:
                from .rosmaster_skill_features import rgb_image
                pixels = rgb_image(frame['rgb_record'], lambda name: corpus.read(episode, name))
                if hashlib.sha256(np.asarray(pixels).tobytes()).hexdigest() in source_pixels:
                    raise PipelineBlocked('SOURCE_RGB_CANNOT_SUBSTITUTE_TARGET_OBSERVATION')
        self.corpus = corpus
        self.binding = binding
        self.domain = target_domain
        self.source_pixels_checked = bool(source_pixels)

    def readiness(self):
        count = len(self.corpus.train)
        return dict(schema='explorer_transfer_target_corpus_readiness_v1',
                    skill_id=self.corpus.skill_id, target_version=self.corpus.version,
                    target_domain=self.domain, target_action_dimension=9,
                    train_original_demonstrations=count,
                    development_original_demonstrations=len(self.corpus.development),
                    sealed_original_demonstrations=len(self.corpus.sealed_ids),
                    budgets=[dict(demonstrations=n, ready=count >= n,
                                  missing_original_demonstrations=max(0, n-count)) for n in BUDGETS],
                    source_model_binding=deepcopy(self.binding),
                    source_RGB_pixel_overlap_checked=self.source_pixels_checked,
                    source_and_target_camera_domains_assumed_equal=False,
                    source_action_head_reused=False, source_object_skill_inferred=False,
                    physical_execution_authorized=False, target_policy_trained=False)

    def selection(self, demonstrations, *, seed=41):
        if type(demonstrations) is not int or demonstrations not in BUDGETS:
            raise PipelineBlocked('TARGET_DEMONSTRATION_BUDGET_MUST_BE_5_10_25')
        if type(seed) is not int or not 0 <= seed <= 1000000:
            raise PipelineBlocked('BOUNDED_TARGET_SELECTION_SEED_REQUIRED')
        if len(self.corpus.train) < demonstrations:
            raise PipelineBlocked('INSUFFICIENT_ORIGINAL_TARGET_DEMONSTRATIONS')
        self.corpus.recheck()
        ordered = sorted(self.corpus.train, key=lambda row: digest(dict(
            seed=seed, original_episode_id=row['descriptor']['source_episode_id'])))
        selected = [row['descriptor']['source_episode_id'] for row in ordered[:demonstrations]]
        manifest = deepcopy(self.corpus.manifest)
        manifest['episodes'] = [row for row in manifest['episodes']
                                if row['split'] == 'development' or row['source_episode_id'] in selected]
        manifest['transfer_target_selection'] = dict(schema=SCHEMA,
            source_model_binding=deepcopy(self.binding), parent_aggregate_sha256=self.corpus.sha256,
            target_domain=self.domain, target_version=self.corpus.version,
            selection_seed=seed, original_demonstration_budget=demonstrations,
            training_original_episode_ids=selected,
            development_original_episode_ids=[row['descriptor']['source_episode_id']
                                               for row in self.corpus.development],
            sealed_original_episode_ids=list(self.corpus.sealed_ids),
            ranking='sha256_of_seed_and_original_episode_id_no_outcome_or_DEV_optimization',
            source_action_head_reused=False, physical_execution_authorized=False)
        return manifest

    def selection_reference(self, demonstrations, *, seed=41):
        """Read-only expected view identity; never repairs missing artifacts."""
        manifest = self.selection(demonstrations, seed=seed)
        checksum = digest(manifest)
        return dict(dataset_manifest=str(self.corpus.path.with_name('transfer-target-'+checksum+'.json')),
                    dataset_sha256=checksum,selection=manifest['transfer_target_selection'])

    def save_selection(self, demonstrations, *, seed=41):
        """New immutable sibling view: relative original mappings stay unchanged."""
        manifest = self.selection(demonstrations, seed=seed)
        raw = canonical(manifest)
        path = self.corpus.path.with_name('transfer-target-'+hashlib.sha256(raw).hexdigest()+'.json')
        from explorer_shared.cooperative_recording05 import write_original
        with path.with_suffix('.lock').open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            if path.exists():
                if path.read_bytes() != raw:
                    raise PipelineBlocked('IMMUTABLE_TARGET_SELECTION_CONFLICT')
            else:
                write_original(path, raw)
        self.corpus.recheck()
        # Re-run the SAME reader on the resulting view: normalizer fits only its
        # chosen TRAIN originals, never excluded train/DEV/sealed frames.
        selected = CanonicalCorpus(path)
        return dict(dataset_manifest=str(path), dataset_sha256=selected.sha256,
                    selection=manifest['transfer_target_selection'])
