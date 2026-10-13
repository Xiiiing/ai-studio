#!/usr/bin/env python3
"""Export a synthetic CPU-only profile demo; no model/GPU/paid service is called."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main():
    from factory.production_profiles import PRESETS
    from profile_fixtures import run_mock_profile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=list(PRESETS), default="compact6")
    parser.add_argument("--output", type=Path, required=True, help="New directory inside this checkout's runtime directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "runtime").resolve()) or output.exists():
        parser.error("output must be a new directory inside this checkout's runtime; existing files are never overwritten")
    summary, _, _ = run_mock_profile(args.preset, output)
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
