#!/usr/bin/env python3
"""Promote only external metadata after immutable artifact acceptance succeeds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET

from source_accounting import digest


def atomic_write(path, data):
    """Replace only the sidecar directory entry, never follow an output alias."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Release asset must be a regular file: ' + str(path))
    return path


def regression_result(path):
    root = ET.parse(regular(path)).getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('.//testsuite')
    counts = {key: sum(int(suite.get(key, 0)) for suite in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if counts['tests'] <= counts['skipped'] or counts['failures'] or counts['errors']:
        raise ValueError('Mandatory regression suite is empty or has failures/errors')
    return {'sha256': digest(path), **counts, 'passed': counts['tests'] - counts['skipped']}


def tracked_source_inventory(root):
    from build_appimage import safe_source_path
    status = subprocess.run(['git', '-C', root, 'status', '--porcelain', '--untracked-files=no'],
                            check=True, capture_output=True, text=True).stdout
    if status:
        raise ValueError('Selected source checkout has tracked modifications')
    result = subprocess.run(['git', '-C', root, 'ls-tree', '-r', '--name-only', '-z', 'HEAD'],
                            check=True, capture_output=True).stdout
    expected = {}
    for raw in result.split(b'\0'):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if not safe_source_path(name) or name.startswith('design/'):
            continue
        path = regular(root / name)
        expected[name] = {'sha256': digest(path), 'mode': 0o755 if path.stat().st_mode & 0o111 else 0o644}
    return expected


def verify_application_archive(archive, declared, root):
    expected = tracked_source_inventory(root)
    if declared != expected:
        raise ValueError('Declared application source inventory differs from clean selected source')
    seen = set()
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            if not member.name.startswith('pbdesk-source/') or not member.isfile():
                raise ValueError('Unexpected application source archive entry')
            name = member.name.removeprefix('pbdesk-source/')
            if name not in expected or name in seen or member.mode != expected[name]['mode']:
                raise ValueError('Application source archive membership/mode differs from selected source')
            with source.extractfile(member) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected[name]['sha256']:
                    raise ValueError('Application source archive bytes differ from selected source')
            seen.add(name)
    if seen != set(expected):
        raise ValueError('Application source archive omits selected source/build inputs')


def verify_license_archive(archive, sealed_notices):
    expected_files, expected_dirs = {}, {'': sealed_notices.stat().st_mode & 0o777}
    for path in sealed_notices.rglob('*'):
        name = path.relative_to(sealed_notices).as_posix()
        if path.is_symlink():
            raise ValueError('Unexpected symlink in sealed license tree')
        if path.is_dir():
            expected_dirs[name] = path.stat().st_mode & 0o777
        else:
            expected_files[name] = (digest(path), path.stat().st_mode & 0o777)
    seen_files, seen_dirs = set(), set()
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            if member.name == 'pbdesk-licenses':
                name = ''
            elif member.name.startswith('pbdesk-licenses/'):
                name = member.name.removeprefix('pbdesk-licenses/')
            else:
                raise ValueError('Unexpected license archive root')
            if member.isdir():
                if name not in expected_dirs or name in seen_dirs or member.mode != expected_dirs[name]:
                    raise ValueError('License archive directories differ from sealed notices')
                seen_dirs.add(name)
            elif member.isfile():
                if name not in expected_files or name in seen_files or member.mode != expected_files[name][1]:
                    raise ValueError('License archive membership/mode differs from sealed notices')
                with source.extractfile(member) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_files[name][0]:
                        raise ValueError('License archive bytes differ from sealed notices')
                seen_files.add(name)
            else:
                raise ValueError('Non-regular license archive entry')
    if seen_files != set(expected_files) or seen_dirs != set(expected_dirs):
        raise ValueError('License archive omits actual sealed notices')


def verify_source_parts(directory, ledger, root):
    """Check original archive/patch bytes, not just a claimed completeness flag."""
    expected = {}
    for record in ledger['upstream_materials']:
        if not record.get('notice_only'):
            expected.setdefault(record['file'], set()).add(record['sha256'])
    for package in ledger['ubuntu_packages']:
        for record in package.get('corresponding_source', {}).get('files', []):
            expected.setdefault(record['file'], set()).add(record['sha256'])
    for name in ('REBUILDING.md', 'source-policy.json'):
        expected[name] = {digest(root / 'packaging' / name)}
    observed, member_names = set(), set()
    archives = ledger.get('source_archives')
    if not isinstance(archives, dict) or not archives:
        raise ValueError('Applicable corresponding-source archives are missing')
    for name, expected_digest in archives.items():
        if Path(name).name != name:
            raise ValueError('Unsafe corresponding-source archive name')
        path = regular(directory / name)
        if digest(path) != expected_digest:
            raise ValueError('Corresponding-source archive SHA256 mismatch')
        with tarfile.open(path, 'r:*') as archive:
            for member in archive:
                member_path = Path(member.name)
                if (not member.isfile() or member_path.is_absolute() or '..' in member_path.parts
                        or member.name in member_names):
                    raise ValueError('Unsafe/duplicate corresponding-source member')
                member_names.add(member.name)
                with archive.extractfile(member) as stream:
                    value = hashlib.file_digest(stream, 'sha256').hexdigest()
                if value not in expected.get(member_path.name, set()):
                    raise ValueError('Unexpected or changed original source/patch bytes: ' + member.name)
                observed.add((member_path.name, value))
    if observed != {(name, value) for name, values in expected.items() for value in values}:
        raise ValueError('Required original source/patch/rebuild inputs are missing')
    policy = json.loads((root / 'packaging/source-policy.json').read_text())
    by_component = {record['component']: record for record in ledger['upstream_materials'] if not record.get('notice_only')}
    for record in policy['archives']:
        if not record.get('required_source'):
            continue
        actual = by_component.get(record['id'], {})
        if actual.get('version') != record['version'] or (record.get('sha256') and actual.get('sha256') != record['sha256']) or (record.get('git_commit') and actual.get('git_commit') != record['git_commit']):
            raise ValueError('Required matching source policy input is absent: ' + record['id'])
    if ledger.get('complete') is not True or ledger.get('blockers'):
        raise ValueError('Applicable source/notices accounting is incomplete')
    return archives


def sealed_source_anchor(image, info, ledger, ledger_hash, root, licenses):
    """Verify source evidence against files sealed inside the tested image."""
    expected_toolchain = json.loads((root / 'packaging/toolchain.json').read_text())
    if info.get('toolchain') != expected_toolchain:
        raise ValueError('Build toolchain differs from independently reviewed source policy')
    offset = info['appimage_runtime_size_bytes']
    if type(offset) is not int or offset < 4096 or offset >= image.stat().st_size:
        raise ValueError('Invalid outer AppImage runtime size')
    with image.open('rb') as stream:
        prefix = stream.read(offset)
    if (hashlib.sha256(prefix).hexdigest() != info['appimage_runtime_sha256']
            or info['appimage_runtime_sha256'] != info['toolchain']['appimage_runtime']['sha256']
            or prefix[8:11] != b'AI\x02'):
        raise ValueError('Outer AppImage runtime differs from the reviewed official pin')
    with tempfile.TemporaryDirectory(prefix='pbdesk-final-assets-') as temp:
        extracted = Path(temp) / 'image'
        subprocess.run(['unsquashfs', '-processors', '2', '-no-xattrs', '-o', str(offset),
                        '-d', str(extracted), str(image), 'usr/pbdesk'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
        runtime = extracted / 'usr/pbdesk'
        from pbdesk.packaged import verify_runtime
        if verify_runtime(runtime) != info['runtime_id']:
            raise ValueError('Inner runtime identity differs from accepted artifact')
        verify_license_archive(licenses, runtime / '_internal/licenses/distribution')
        anchor = json.loads((runtime / '_internal/licenses/distribution/SOURCE-COMPLIANCE.json').read_text())
        if (anchor.get('source_manifest_sha256') != ledger_hash or anchor.get('source_archives') != ledger['source_archives']
                or anchor.get('corresponding_source_complete') is not True or anchor.get('blockers')):
            raise ValueError('Source material does not match the immutable in-image anchor')
        expected = {entry['path']: entry['sha256'] for entry in ledger['native_elf_files']}
        observed = {}
        for path in runtime.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as stream:
                if stream.read(4) == b'\x7fELF':
                    observed[path.relative_to(runtime).as_posix()] = digest(path)
        if observed != expected:
            raise ValueError('Source ledger does not cover every actual frozen ELF')


def validate_trial(report, *, image_hash, revision, baseline):
    from verify_release import REQUIRED_CHECKS, validate_measurements
    old, new = report.get('artifacts', {}).get('old', {}), report.get('artifacts', {}).get('new', {})
    if (report.get('schema_version') != 1 or report.get('kind') != 'packaged-release-acceptance'
            or report.get('classification') != 'trial' or report.get('relation') != 'same' or report.get('status') != 'pass'
            or any(old.get(key) != value for key, value in baseline.items())
            or new.get('version') != baseline['version'] or new.get('sha256') != image_hash
            or new.get('source_revision') != revision
            or any(new.get('observed_runtime', {}).get(key) != new.get(key)
                   for key in ('runtime_id', 'appimage_runtime_sha256', 'version'))):
        raise ValueError('Same-version trial does not bind the selected published baseline and actual candidate')
    checks = report.get('checks', [])
    by_id = {check.get('id'): check for check in checks}
    if len(by_id) != len(checks) or not set(REQUIRED_CHECKS) <= set(by_id) or any(check.get('status') != 'pass' for check in checks):
        raise ValueError('Same-version trial lacks successful measured outcomes')
    validate_measurements({key: by_id[key].get('evidence', {}) for key in REQUIRED_CHECKS}, image_hash)
    return {'accepted_runtime_id': new['runtime_id'], 'accepted_appimage_runtime_sha256': new['appimage_runtime_sha256'],
            'required_outcomes': len(REQUIRED_CHECKS)}


def expected_lockfiles(root):
    return {name: digest(root / name) for name in ('requirements-build.lock',
            'packaging/requirements-runtime.lock', 'packaging/requirements-test.lock')}


def verified_lockfiles(root, declared):
    expected = expected_lockfiles(root)
    if declared != expected:
        raise ValueError('Complete mandatory dependency lockfile mapping differs from selected source')
    return expected


def assess(directory, *, root, classification="release"):
    from baseline import validate_selection
    from verify_release import validate_acceptance, validate_smoke
    directory, root = Path(directory).resolve(), Path(root).resolve()
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    prefix = 'pbdesk-' + version
    info_path = regular(directory / (prefix + '-build-info.json'))
    info = json.loads(info_path.read_text())
    policy = json.loads((root / 'packaging/release-readiness.json').read_text())
    if (policy.get('schema_version') != 2 or policy.get('target_version') != version
            or policy.get('release_authorized') is not True or policy.get('owner_acceptance', {}).get('approved') is not True):
        raise ValueError('Explicit version/publication/owner policy is absent')
    revision = subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    if info.get('version') != version or info.get('source_revision') != revision or info.get('source_status'):
        raise ValueError('Release assets differ from the selected clean source revision/version')
    selected = json.loads(regular(directory / 'baseline-selection.json').read_text())
    baseline = validate_selection(policy, selected, version, allow_same=classification == 'trial')
    image = regular(directory / (prefix + '-x86_64.AppImage'))
    image_hash = digest(image)
    acceptance_path = regular(directory / 'release-acceptance.json')
    acceptance = json.loads(acceptance_path.read_text())
    if classification == 'release':
        accepted = validate_acceptance(acceptance, new_sha256=image_hash, source_revision=revision, baseline=baseline)
    elif classification == 'trial':
        accepted = validate_trial(acceptance, image_hash=image_hash, revision=revision, baseline=baseline)
    else:
        raise ValueError('Unknown release evidence classification')
    new = acceptance['artifacts']['new']
    if (new['version'] != version or accepted['accepted_runtime_id'] != info['runtime_id']
            or accepted['accepted_appimage_runtime_sha256'] != info['appimage_runtime_sha256']):
        raise ValueError('Actual accepted runtime/version differs from build inputs')
    source = regular(directory / (prefix + '-source.tar.gz'))
    licenses = regular(directory / (prefix + '-licenses.tar.gz'))
    ledger_path = regular(directory / (prefix + '-corresponding-source.json'))
    ledger_hash = digest(ledger_path)
    ledger = json.loads(ledger_path.read_text())
    if ledger['source_revision'] != revision:
        raise ValueError('Source ledger belongs to another revision')
    archives = verify_source_parts(directory, ledger, root)
    measured_materials = {'source_archive_sha256': digest(source), 'licenses_archive_sha256': digest(licenses),
                          'corresponding_source_manifest_sha256': ledger_hash,
                          'corresponding_source_archives': archives, 'lockfiles': verified_lockfiles(root, info['lockfiles'])}
    if new['source_materials'] != measured_materials:
        raise ValueError('Accepted source/license materials differ from actual release assets')
    if info.get('source_materials') != {key: measured_materials[key] for key in ('corresponding_source_manifest_sha256', 'corresponding_source_archives')}:
        raise ValueError('Build material identity differs from accepted source evidence')
    verify_application_archive(source, info['source_files'], root)
    sealed_source_anchor(image, info, ledger, ledger_hash, root, licenses)
    smoke_path = regular(directory / 'smoke-evidence.json')
    smoke = validate_smoke(smoke_path, image_hash)
    regression = regression_result(directory / 'regression-results.xml')
    return {'schema_version': 1, 'status': 'pass', 'classification': classification, 'version': version, 'source_revision': revision,
            'appimage_sha256': image_hash, 'runtime_id': info['runtime_id'],
            'appimage_runtime_sha256': info['appimage_runtime_sha256'],
            'source_materials': measured_materials, 'acceptance_report_sha256': digest(acceptance_path),
            'expected_baseline': baseline, 'required_acceptance_outcomes': accepted['required_outcomes'],
            'smoke': smoke, 'regression': regression,
            'owner_acceptance': policy['owner_acceptance'],
            'source_scope': ledger['scope'], 'provenance_limitations': ledger['provenance_limitations']}


def checksums(directory, version):
    target = directory / f'pbdesk-{version}-SHA256SUMS'
    files = [path for path in directory.iterdir() if path.is_file() and path != target]
    atomic_write(target, ''.join(digest(regular(path)) + '  ' + path.name + '\n' for path in sorted(files)).encode())


def finalize(directory, *, root, classification="release"):
    directory = Path(directory).resolve()
    proof = assess(directory, root=root, classification=classification)
    prefix = 'pbdesk-' + proof['version']
    proof_path = directory / (prefix + '-readiness.json')
    write_json(proof_path, proof)
    info_path = directory / (prefix + '-build-info.json')
    info = json.loads(info_path.read_text())
    info.update(candidate=classification != 'release', final_release_ready=classification == 'release',
                release_evidence={'file': proof_path.name, 'sha256': digest(proof_path)})
    info['source_compliance']['final_release_ready'] = classification == 'release'
    write_json(info_path, info)
    checksums(directory, proof['version'])
    return proof


def validate_bundle(directory, *, root, classification="release"):
    directory = Path(directory).resolve()
    proof = assess(directory, root=root, classification=classification)
    prefix = 'pbdesk-' + proof['version']
    proof_path = regular(directory / (prefix + '-readiness.json'))
    info = json.loads((directory / (prefix + '-build-info.json')).read_text())
    if (json.loads(proof_path.read_text()) != proof or info.get('candidate') is not (classification != 'release')
            or info.get('final_release_ready') is not (classification == 'release')
            or info.get('release_evidence') != {'file': proof_path.name, 'sha256': digest(proof_path)}):
        raise ValueError('External final readiness proof is absent or changed')
    sums = regular(directory / (prefix + '-SHA256SUMS'))
    recorded = {}
    for line in sums.read_text().splitlines():
        value, name = line.split('  ', 1)
        if Path(name).name != name or name in recorded or digest(regular(directory / name)) != value:
            raise ValueError('Final release asset checksums are invalid')
        recorded[name] = value
    if set(recorded) != {path.name for path in directory.iterdir() if path.is_file() and path != sums}:
        raise ValueError('Final release checksum inventory is incomplete')
    return proof


def validate_final_bundle(directory, *, root):
    # The tag/draft guard deliberately has no trial-classification option.
    return validate_bundle(directory, root=root, classification='release')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--classification', choices=('release', 'trial'), default='release')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    proof = (validate_bundle if args.verify_only else finalize)(args.assets, root=root, classification=args.classification)
    print(json.dumps({'status': proof['status'], 'version': proof['version'], 'sha256': proof['appimage_sha256']}))


if __name__ == '__main__':
    main()
