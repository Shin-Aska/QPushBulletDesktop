#!/usr/bin/env python3
"""Select an independently pinned upgrade or same-version candidate baseline."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from source_accounting import digest, write_json


def gh_json(endpoint):
    return json.loads(subprocess.run(['gh', 'api', endpoint], check=True, capture_output=True, text=True).stdout)


def stable_version(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value or ''):
        raise ValueError('A stable exact semantic version is required')
    return tuple(map(int, value.split('.')))


def published_selection(policy, release):
    repository = policy['repository']
    version = release['tag_name'].removeprefix('v')
    tag = gh_json(f'repos/{repository}/git/ref/tags/{release["tag_name"]}')['object']
    while tag['type'] == 'tag':
        tag = gh_json(f'repos/{repository}/git/tags/{tag["sha"]}')['object']
    if tag['type'] != 'commit':
        raise ValueError('Published baseline tag does not resolve to a commit')
    asset = next((a for a in release['assets'] if a['name'] == f'pbdesk-{version}-x86_64.AppImage'), None)
    if not asset or not re.fullmatch('sha256:[a-f0-9]{64}', asset.get('digest') or ''):
        raise ValueError('Published baseline lacks a GitHub SHA256-bound AppImage asset')
    return {'kind': 'published-release', 'repository': repository, 'version': version,
            'source_revision': tag['sha'], 'sha256': asset['digest'].split(':')[1],
            'tag': release['tag_name'], 'release_id': release['id'], 'asset_id': asset['id']}


def select(policy, target, *, allow_same=False):
    releases = None
    if allow_same:
        releases = gh_json(f'repos/{policy["repository"]}/releases?per_page=100')
        same = next((release for release in releases if not release['draft'] and not release['prerelease']
                     and release['tag_name'] == 'v' + target), None)
        if same:
            return published_selection(policy, same)
    if target == policy['initial_baseline']['target_version']:
        return {**policy['initial_baseline'], 'repository': policy['repository']}
    releases = releases if releases is not None else gh_json(f'repos/{policy["repository"]}/releases?per_page=100')
    choices = [release for release in releases if not release['draft'] and not release['prerelease']
               and re.fullmatch(r'v\d+\.\d+\.\d+', release['tag_name'])
               and stable_version(release['tag_name'][1:]) < stable_version(target)]
    if not choices:
        raise ValueError('No published stable lower-version upgrade baseline exists')
    return published_selection(policy, max(choices, key=lambda r: stable_version(r['tag_name'][1:])))


def validate_selection(policy, selected, target, *, allow_same=False):
    expected = select(policy, target, allow_same=allow_same)
    if selected != expected:
        raise ValueError('Selected baseline does not match the independent release policy')
    if stable_version(selected['version']) > stable_version(target) or (selected['version'] == target and not allow_same):
        raise ValueError('Upgrade baseline must be strictly older than the target')
    return {key: selected[key] for key in ('sha256', 'source_revision', 'version')}


def unpack_baseline(archive, destination):
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = Path(member.filename)
            if path.is_absolute() or '..' in path.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe baseline artifact archive entry')
        source.extractall(destination)


def download_baseline(policy, selected, destination):
    destination.mkdir(parents=True)
    repository = policy['repository']
    if selected['kind'] == 'initial-candidate':
        metadata = gh_json(f'repos/{repository}/actions/artifacts/{selected["artifact_id"]}')
        if metadata['expired'] or metadata['workflow_run']['id'] != selected['workflow_run_id']:
            raise ValueError('Pinned initial baseline artifact is unavailable or mismatched')
        with tempfile.TemporaryDirectory(prefix='pbdesk-baseline-') as temp:
            archive = Path(temp) / 'baseline.zip'
            with archive.open('wb') as output:
                subprocess.run(['gh', 'api', f'repos/{repository}/actions/artifacts/{selected["artifact_id"]}/zip'],
                               check=True, stdout=output)
            unpack_baseline(archive, destination)
    else:
        # Matching sidecars are needed by the independent upgrade verifier.
        subprocess.run(['gh', 'release', 'download', selected['tag'], '--repo', repository,
                        '--dir', str(destination), '--pattern', 'pbdesk-*'], check=True)
    images = list(destination.rglob(f'pbdesk-{selected["version"]}-x86_64.AppImage'))
    if len(images) != 1 or digest(images[0]) != selected['sha256']:
        raise ValueError('Downloaded upgrade baseline does not match the pinned AppImage')
    image = images[0]
    info = json.loads((image.parent / f'pbdesk-{selected["version"]}-build-info.json').read_text())
    if info['source_revision'] != selected['source_revision'] or info['version'] != selected['version']:
        raise ValueError('Baseline sidecar differs from the independently pinned tag/source/version')
    image.chmod(0o755)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--allow-same-published', action='store_true', help='Ordinary candidates may use same-version trial evidence; never a final release gate')
    args = parser.parse_args()
    policy = json.loads((Path(__file__).parent / 'release-readiness.json').read_text())
    selected = select(policy, args.target, allow_same=args.allow_same_published)
    validate_selection(policy, selected, args.target, allow_same=args.allow_same_published)
    image = download_baseline(policy, selected, args.destination)
    write_json(args.selection, selected)
    if args.github_output:
        with args.github_output.open('a') as output:
            for key, value in {'image': str(image.resolve()),
                               'classification': 'trial' if selected['version'] == args.target else 'release', **{k: selected[k] for k in ('sha256', 'source_revision', 'version')}}.items():
                output.write(f'{key}={value}\n')
    print(json.dumps({'baseline': selected, 'image': str(image)}))


if __name__ == '__main__':
    main()
