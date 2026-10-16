#!/usr/bin/env python3
"""Prepare or verify a matching Qwen3.8 projector; never changes running services."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory.vision_components import QWEN38_PROJECTOR, prepare_projector, verify_pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--projector', type=Path)
    parser.add_argument('--download-directory', type=Path)
    args = parser.parse_args()
    if bool(args.projector) == bool(args.download_directory):
        parser.error('Choose --projector or explicit --download-directory')
    projector = args.projector or prepare_projector(args.download_directory)
    print(json.dumps({**verify_pair(args.model, projector), 'projector': str(projector),
                      'pinned_artifact': QWEN38_PROJECTOR, 'services_changed': False}, indent=2))


if __name__ == '__main__':
    main()
