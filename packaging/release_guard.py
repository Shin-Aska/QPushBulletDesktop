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
    required = ('final_release_ready', 'corresponding_source_complete',
                'supported_desktop_acceptance_complete', 'encrypted_profile_upgrade_verified')
    if any(readiness.get(key) is not True for key in required) or readiness.get('blockers'):
        raise ValueError('Final publication is blocked by packaging/release-readiness.json')
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--visibility', required=True, choices=['private', 'public'])
    args = parser.parse_args()
    print(validate(Path(__file__).resolve().parents[1], args.tag, private=args.visibility != 'public'))


if __name__ == '__main__':
    main()
