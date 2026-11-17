#!/usr/bin/env python3
"""Fail closed before any final draft release can be created."""
import argparse
import json
from pathlib import Path
import re
import tomllib


def validate(root, tag, *, private):
    if private:
        raise ValueError('Final releases require a deliberate public repository transition')
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    if tag != 'v' + version or not re.fullmatch(r'v[1-9]\d*\.\d+\.\d+', tag):
        raise ValueError('Final tag must exactly match a stable project version >= 1.0.0')
    readiness = json.loads((root / 'packaging/release-readiness.json').read_text())
    if (readiness.get('schema_version') != 2 or readiness.get('target_version') != version
            or readiness.get('release_authorized') is not True
            or readiness.get('owner_acceptance', {}).get('approved') is not True
            or not readiness.get('required_evidence')):
        raise ValueError('Final publication is blocked by packaging/release-readiness.json')
    # These are authorization/policy checks only. Actual source, upgrade and test
    # evidence is validated against immutable artifact bytes by the finalizer.
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--visibility', required=True, choices=['private', 'public'])
    parser.add_argument('--assets', type=Path, help='Require a fully verified final bundle before draft creation')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = validate(root, args.tag, private=args.visibility != 'public')
    if args.assets:
        from finalize_release import validate_final_bundle
        validate_final_bundle(args.assets, root=root)
    print(version)


if __name__ == '__main__':
    main()
