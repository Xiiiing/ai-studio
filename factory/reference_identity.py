"""Separate approved reference facts from replaceable review evidence.

The original bindings remain audit records. Only this checked projection is
used for generation identity, including comparisons with historical plans.
"""
from __future__ import annotations

import copy
from pathlib import Path

from studio.core import sha256, within
from .creative_evidence_io import read_json
from .scheduler import ProductionNeedsDecision


def generation_identity(value, *, root, _manifests=()):
    if isinstance(value, list):
        return [generation_identity(item, root=root, _manifests=_manifests) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    manifest = value.get('composition_manifest')
    if manifest is None:
        return {key: generation_identity(item, root=root, _manifests=_manifests)
                for key, item in value.items()}
    try:
        if not isinstance(manifest, dict) or not isinstance(manifest.get('path'), str):
            raise ValueError('Missing composition manifest identity')
        path = within(Path(root), manifest['path'])
        if path in _manifests or len(_manifests) >= 16:
            raise ValueError('Cyclic or excessive composition references')
        if sha256(path) != manifest.get('sha256'):
            raise ValueError('Composition manifest checksum changed')
        document = read_json(path)
        output, requirements = document.get('output'), document.get('requirements')
        if (document.get('schema_version') != 1 or not isinstance(output, dict)
                or not isinstance(requirements, dict) or requirements.get('kind') != 'composition'
                or any(output.get(key) != value.get(key) for key in ('path', 'sha256'))):
            raise ValueError('Composition manifest does not belong to these pixels')
        media = within(Path(root), output['path'])
        if sha256(media) != output['sha256']:
            raise ValueError('Composition pixels changed')
        # Audit fields have meaning only on a verified composition output.
        # Keep every other field, including unknown future generation facts.
        result = {key: generation_identity(item, root=root, _manifests=_manifests)
                  for key, item in value.items()
                  if key not in {'review_signature', 'qa_sha256', 'composition_manifest'}}
        result['composition_requirements'] = generation_identity(
            requirements, root=root, _manifests=(*_manifests, path))
        return result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ProductionNeedsDecision('组合参考图证据校验失败，未重新编译或生成',
                                      'reference_identity_invalid') from exc
