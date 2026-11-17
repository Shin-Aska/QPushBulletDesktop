"""Source/provenance integrity checks; no network, compilation or installations."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pbdesk_source_accounting', ROOT / 'packaging/source_accounting.py')
accounting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounting)


def test_native_inventory_binds_actual_analysis_inputs_and_transformed_output(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    (runtime / '_internal').mkdir(parents=True)
    (runtime / 'pbdesk').write_bytes(b'\x7fELFfrozen application')
    (runtime / '_internal/library.so').write_bytes(b'\x7fELFtransformed RPATH')
    source = tmp_path / 'python/lib/library.so'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'\x7fELForiginal RPATH')
    monkeypatch.setattr(accounting.importlib.metadata, 'version', lambda name: '6.22.3')
    inputs = {'binaries': [{'destination': 'library.so', 'source': str(source), 'type': 'BINARY'}]}
    result = accounting.native_inventory(runtime, inputs, base_prefix=tmp_path / 'python')
    library = next(row for row in result if row['path'].endswith('library.so'))
    assert library['origin'] == {'kind': 'cpython', 'version': '.'.join(map(str, accounting.sys.version_info[:3])),
                                 'archive_member': 'lib/library.so'}
    assert library['input_sha256'] == accounting.digest(source)
    assert library['sha256'] == accounting.digest(runtime / '_internal/library.so')
    assert library['input_sha256'] != library['sha256']
    (runtime / '_internal/unmapped.so').write_bytes(b'\x7fELFunknown')
    with pytest.raises(ValueError, match='lacks Analysis provenance'):
        accounting.native_inventory(runtime, inputs, base_prefix=tmp_path / 'python')


def test_dsc_checksums_include_original_source_and_downstream_patches():
    raw = '-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nFormat: 3.0 (quilt)\nChecksums-Sha256:\n ' + 'a' * 64 + ' 13 package.orig.tar.xz\n ' + 'b' * 64 + ' 7 package.debian.tar.xz\nFiles:\n other data\n'
    result = accounting.dsc_checksums(raw)
    assert result == {'package.orig.tar.xz': {'sha256': 'a' * 64, 'size_bytes': 13},
                      'package.debian.tar.xz': {'sha256': 'b' * 64, 'size_bytes': 7}}


@pytest.mark.parametrize('filename', ['../escape.tar.xz', '/absolute.tar.xz'])
def test_dsc_rejects_source_path_escape(filename):
    with pytest.raises(ValueError, match='Unsafe Ubuntu source'):
        accounting.dsc_checksums('Checksums-Sha256:\n ' + 'a' * 64 + ' 1 ' + filename + '\n')


def make_tar(path, members):
    with tarfile.open(path, 'w:gz') as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_pinned_git_archive_requires_every_exact_blob(tmp_path, monkeypatch):
    raw = b'actual upstream source\n'
    archive = tmp_path / 'source.tar.gz'
    make_tar(archive, {'upstream-sha/runtime.c': raw})
    expected = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    monkeypatch.setattr(accounting, 'request_json', lambda url: {'tree': [{'path': 'runtime.c', 'type': 'blob', 'sha': expected}]})
    accounting.verify_git_archive(archive, 'official/runtime', 'a' * 40)
    make_tar(archive, {'upstream-sha/runtime.c': b'tampered'})
    with pytest.raises(ValueError, match='blob mismatch'):
        accounting.verify_git_archive(archive, 'official/runtime', 'a' * 40)


def test_notice_extraction_never_unpacks_source_or_traversal(tmp_path):
    source = tmp_path / 'source.tar.gz'
    make_tar(source, {'upstream/LICENSE': b'actual notice', 'upstream/build.sh': b'never run this',
                      'upstream/../../escape-LICENSE': b'bad'})
    destination = tmp_path / 'notices'
    assert accounting.archive_notices(source, destination) == ['LICENSE']
    assert (destination / 'LICENSE').read_bytes() == b'actual notice'
    assert not (destination / 'build.sh').exists()
    assert not (tmp_path / 'escape-LICENSE').exists()


def test_split_source_parts_preserve_original_archives_bytes(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'first.tar.xz').write_bytes(b'a' * 30)
    (source / 'second.tar.xz').write_bytes(b'b' * 30)
    parts = accounting.source_parts(source, tmp_path, 'pbdesk-1.0.0', limit=40)
    assert len(parts) == 2
    recovered = {}
    for name, expected in parts.items():
        assert accounting.digest(tmp_path / name) == expected
        with tarfile.open(tmp_path / name) as archive:
            for member in archive:
                recovered[member.name] = archive.extractfile(member).read()
    assert recovered == {'first.tar.xz': b'a' * 30, 'second.tar.xz': b'b' * 30}


def test_locked_mpl_certifi_requires_matching_sdist_source():
    import importlib.metadata
    import json
    policy = json.loads((ROOT / 'packaging/source-policy.json').read_text())
    rule = accounting.wheel_source_rule(importlib.metadata.distribution('certifi'), policy)
    assert rule['kind'] == 'sdist'
    assert rule['observed_license'] == 'MPL-2.0'
    del policy['source_license_rules']['certifi']
    with pytest.raises(ValueError, match='Unclassified source-license obligation'):
        accounting.wheel_source_rule(importlib.metadata.distribution('certifi'), policy)


def test_qt_wheel_requires_module_and_compiled_translation_sources():
    import importlib.metadata
    import json
    policy = json.loads((ROOT / 'packaging/source-policy.json').read_text())
    distribution = importlib.metadata.distribution('PyQt6-Qt6')
    rule = accounting.wheel_source_rule(distribution, policy)
    assert rule['kind'] == 'module-archives'
    assert 'qttranslations' in rule['components']
    policy['archives'] = [item for item in policy['archives'] if item['id'] != 'qttranslations']
    with pytest.raises(ValueError, match='module/translation sources are incomplete'):
        accounting.wheel_source_rule(distribution, policy)


@pytest.mark.parametrize('license', ['GPL-3.0-only', 'LGPL-2.1-only', 'MPL-2.0'])
def test_unreviewed_future_copyleft_dependency_fails_closed(license):
    from email.message import Message
    from types import SimpleNamespace
    metadata = Message()
    metadata['Name'] = 'new-dependency'
    metadata['License-Expression'] = license
    with pytest.raises(ValueError, match='Unclassified source-license obligation'):
        accounting.wheel_source_rule(SimpleNamespace(metadata=metadata, version='1.0.0'), {})


def test_r_efi_style_authors_file_preserves_actual_license_and_copyright(tmp_path):
    archive = tmp_path / 'r-efi.crate'
    actual_notice = (b'LICENSE: MIT OR Apache-2.0 OR LGPL-2.1-or-later\n'
                     b'AUTHORS-MIT: Permission is hereby granted ...\n'
                     b'COPYRIGHT: Copyright (C) 2017-2023 Red Hat, Inc.\n')
    make_tar(archive, {'r-efi-5.2.0/AUTHORS': actual_notice,
                       'r-efi-5.2.0/src/lib.rs': b'not executed'})
    destination = tmp_path / 'notices'
    assert accounting.archive_notices(archive, destination) == ['AUTHORS']
    assert (destination / 'AUTHORS').read_bytes() == actual_notice
    assert accounting.license_text_present(destination, ['AUTHORS'])


def test_plain_authors_list_cannot_satisfy_missing_license_text(tmp_path):
    (tmp_path / 'AUTHORS').write_text('Alice Example\nBob Example\n')
    assert not accounting.license_text_present(tmp_path, ['AUTHORS'])


def workspace_fixture(tmp_path, monkeypatch):
    import json
    original = b'[package]\nversion = { workspace = true }\n'
    normalized = b'[package]\nname="example"\nversion="1.0.0"\nrepository="https://github.com/owner/workspace"\nlicense="MIT"\n'
    workspace = b'[workspace.package]\nversion="1.0.0"\nlicense="MIT"\n'
    notice = b'Copyright Example\nPermission is hereby granted to use this software.\n'
    archive = tmp_path / 'example.crate'
    def write(vcs='a'*40):
        make_tar(archive, {'example-1.0.0/.cargo_vcs_info.json': json.dumps({'git': {'sha1': vcs}, 'path_in_vcs': 'crates/example'}).encode(),
                           'example-1.0.0/Cargo.toml.orig': original, 'example-1.0.0/Cargo.toml': normalized})
    write()
    purl = 'pkg:cargo/example@1.0.0'
    rule = {'crate_sha256': accounting.digest(archive), 'git_commit': 'a'*40, 'path_in_vcs': 'crates/example',
            'repository': 'https://github.com/owner/workspace',
            'original_manifest_sha256': hashlib.sha256(original).hexdigest(),
            'workspace_manifest': {'url': 'memory:workspace', 'sha256': hashlib.sha256(workspace).hexdigest()},
            'notices': [{'url': 'memory:license', 'path': 'LICENSE-MIT', 'sha256': hashlib.sha256(notice).hexdigest()}]}
    data = {'memory:workspace': workspace, 'memory:license': notice}
    def download(url, target, expected):
        assert hashlib.sha256(data[url]).hexdigest() == expected
        target.write_bytes(data[url])
    monkeypatch.setattr(accounting, 'download', download)
    return archive, purl, rule, data, write


def test_exact_workspace_notice_recovery_preserves_actual_license(tmp_path, monkeypatch):
    archive, purl, rule, _, _ = workspace_fixture(tmp_path, monkeypatch)
    destination = tmp_path / 'notices'
    notices, evidence = accounting.workspace_notice_fallback(archive, purl, {'crate_notice_overrides': {purl: rule}}, destination)
    assert notices == ['LICENSE-MIT']
    assert accounting.license_text_present(destination, notices)
    assert evidence['git_commit'] == 'a'*40
    assert evidence['notices']['LICENSE-MIT'] == accounting.digest(destination / 'LICENSE-MIT')


@pytest.mark.parametrize('change', ['unknown-crate', 'hash', 'vcs', 'workspace-license'])
def test_workspace_fallback_cannot_waive_unreviewed_or_mismatched_provenance(tmp_path, monkeypatch, change):
    archive, purl, rule, data, write = workspace_fixture(tmp_path, monkeypatch)
    policy = {'crate_notice_overrides': {purl: rule}}
    if change == 'unknown-crate':
        purl = 'pkg:cargo/unreviewed@1.0.0'
    elif change == 'hash':
        rule['crate_sha256'] = 'f'*64
    elif change == 'vcs':
        write('b'*40)
        rule['crate_sha256'] = accounting.digest(archive)
    else:
        data['memory:workspace'] = b'[workspace.package]\nversion="1.0.0"\nlicense="BSD-3-Clause"\n'
        rule['workspace_manifest']['sha256'] = hashlib.sha256(data['memory:workspace']).hexdigest()
    with pytest.raises(ValueError):
        accounting.workspace_notice_fallback(archive, purl, policy, tmp_path / 'notices')


def test_rust_copyleft_alternative_requires_explicit_reviewed_selection():
    component = {'purl': 'pkg:cargo/self_cell@1.3.0',
                 'licenses': [{'expression': 'Apache-2.0 OR GPL-2.0-only'}]}
    with pytest.raises(ValueError, match='Unclassified Rust source-license'):
        accounting.rust_license_choice(component, {})
    policy = {'rust_license_choices': {component['purl']: {'expression': 'Apache-2.0 OR GPL-2.0-only', 'selected': 'Apache-2.0'}}}
    assert accounting.rust_license_choice(component, policy) == 'Apache-2.0'
    component['licenses'][0]['expression'] = 'Apache-2.0 AND GPL-2.0-only'
    with pytest.raises(ValueError, match='alternative changed'):
        accounting.rust_license_choice(component, policy)


def ubuntu_entry(source, version, *, required=True):
    return {'source_package': source, 'source_version': version, 'source_required': required}


def test_ubuntu_source_pairs_are_deduplicated_and_shared_across_binary_packages(tmp_path, monkeypatch):
    calls = []
    def fetch(origin, destination):
        calls.append((origin['source_package'], origin['source_version'], destination))
        assert destination.is_dir()
        return {'source_version': origin['source_version'], 'files': [{'file': 'original.tar.xz', 'sha256': 'a'*64}]}
    monkeypatch.setattr(accounting, 'ubuntu_source', fetch)
    packages = {'libshared-b': ubuntu_entry('shared-source', '1.0-2'),
                'libnotice-only': ubuntu_entry('other-source', '9.0', required=False),
                'libshared-a': ubuntu_entry('shared-source', '1.0-2')}
    result = accounting.collect_ubuntu_sources(packages, tmp_path)
    assert len(calls) == 1
    assert calls[0][:2] == ('shared-source', '1.0-2')
    assert result['libshared-a'] is result['libshared-b']
    assert list(result) == ['libshared-a', 'libshared-b']
    assert 'corresponding_source' not in packages['libshared-a']


def test_ubuntu_source_versions_are_never_merged_or_written_into_same_directory(tmp_path, monkeypatch):
    destinations = []
    def fetch(origin, destination):
        destinations.append(destination)
        return {'source_version': origin['source_version']}
    monkeypatch.setattr(accounting, 'ubuntu_source', fetch)
    result = accounting.collect_ubuntu_sources({'old': ubuntu_entry('shared', '1:1.0-1'),
                                               'new': ubuntu_entry('shared', '1:1.0-2')}, tmp_path)
    assert len(set(destinations)) == 2
    assert result['old']['source_version'] == '1:1.0-1'
    assert result['new']['source_version'] == '1:1.0-2'


def test_ubuntu_source_parallelism_is_bounded_to_four_distinct_writers(tmp_path, monkeypatch):
    import threading
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = peak = started = 0
    def fetch(origin, destination):
        nonlocal active, peak, started
        with lock:
            active += 1
            started += 1
            number = started
            peak = max(peak, active)
        try:
            if number <= 4:
                barrier.wait(timeout=5)
            (destination / 'package.dsc').write_text(origin['source_package'])
            return {'source_version': origin['source_version']}
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(accounting, 'ubuntu_source', fetch)
    packages = {f'lib{i}': ubuntu_entry(f'source{i}', '1.0') for i in range(9)}
    result = accounting.collect_ubuntu_sources(packages, tmp_path)
    assert len(result) == 9 and started == 9 and peak == 4
    assert len(list(tmp_path.rglob('package.dsc'))) == 9


def test_one_failed_required_ubuntu_source_aborts_without_partial_ledger_assignment(tmp_path, monkeypatch):
    from copy import deepcopy
    packages = {'good': ubuntu_entry('available', '1.0'), 'bad': ubuntu_entry('missing', '2.0')}
    original = deepcopy(packages)
    def fetch(origin, destination):
        if origin['source_package'] == 'missing':
            raise ValueError('Exact required source unavailable')
        return {'source_version': origin['source_version'], 'files': []}
    monkeypatch.setattr(accounting, 'ubuntu_source', fetch)
    with pytest.raises(ValueError, match='Exact required source unavailable'):
        accounting.collect_ubuntu_sources(packages, tmp_path)
    assert packages == original
    assert all('corresponding_source' not in package for package in packages.values())


def response_bytes(raw, *, length=None):
    stream = io.BytesIO(raw)
    stream.headers = {'Content-Length': str(len(raw) if length is None else length)}
    return stream


@pytest.mark.parametrize('status', [429, 502, 503])
def test_transient_source_http_retries_then_commits_only_verified_complete_bytes(tmp_path, monkeypatch, status):
    import urllib.error
    raw = b'actual pinned source archive bytes'
    calls, delays = [], []
    def open_url(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, status, 'temporary proxy failure', {}, None)
        return response_bytes(raw)
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', delays.append)
    target = tmp_path / 'source.tar.xz'
    expected = hashlib.sha256(raw).hexdigest()
    result = accounting.download('https://official.invalid/source.tar.xz', target, expected, expected_size=len(raw))
    assert target.read_bytes() == raw and result['sha256'] == expected and result['size_bytes'] == len(raw)
    assert len(calls) == 2 and delays == [0.5]
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('failure', ['read-timeout', 'http-429', 'http-503'])
def test_persistent_partial_source_failure_is_bounded_and_cleans_every_attempt(tmp_path, monkeypatch, failure):
    import urllib.error
    calls, delays = [], []
    class BrokenResponse(io.BytesIO):
        headers = {'Content-Length': '100'}
        def read(self, size=-1):
            if self.tell():
                raise urllib.error.URLError(TimeoutError('temporary read timeout'))
            return super().read(7)
    def open_url(request, timeout):
        calls.append(request.full_url)
        if failure.startswith('http-'):
            raise urllib.error.HTTPError(request.full_url, int(failure.split('-')[1]), 'temporary failure', {}, None)
        return BrokenResponse(b'partial source body')
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', delays.append)
    target = tmp_path / 'source.tar.xz'
    with pytest.raises(urllib.error.URLError):
        accounting.download('https://official.invalid/source.tar.xz', target, 'a'*64, expected_size=100)
    assert len(calls) == 3 and delays == [0.5, 1.0]
    assert not target.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', ['sha256', 'descriptor-size'])
def test_hash_or_descriptor_size_mismatch_is_never_retried_or_exposed(tmp_path, monkeypatch, failure):
    calls = []
    raw = b'unchanged downloaded bytes'
    def open_url(*args, **kwargs):
        calls.append(1)
        return response_bytes(raw)
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', lambda *args: pytest.fail('Integrity failures must not be retried'))
    expected = 'a'*64 if failure == 'sha256' else hashlib.sha256(raw).hexdigest()
    size = len(raw) + 1 if failure == 'descriptor-size' else len(raw)
    with pytest.raises(ValueError, match='mismatch'):
        accounting.download('https://official.invalid/source.tar.xz', tmp_path / 'source.tar.xz', expected, expected_size=size)
    assert len(calls) == 1 and list(tmp_path.iterdir()) == []


def test_json_metadata_timeout_retries_without_weakening_parsing(tmp_path, monkeypatch):
    import urllib.error
    calls, delays = [], []
    def open_url(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError('temporary timeout'))
        return response_bytes(b'{"source_package_version":"1.0-2"}')
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', delays.append)
    assert accounting.request_json('https://official.invalid/metadata?source_name=example&version=1.0-2') == {'source_package_version': '1.0-2'}
    assert calls == [30, 30] and delays == [0.5]


def test_transport_short_body_retries_before_comparing_expected_immutable_digest(tmp_path, monkeypatch):
    raw = b'full source bytes with a pinned digest'
    calls, delays = [], []
    def open_url(*args, **kwargs):
        calls.append(1)
        return response_bytes(raw[:5] if len(calls) == 1 else raw, length=len(raw))
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', delays.append)
    target = tmp_path / 'source.tar.xz'
    result = accounting.download('https://official.invalid/source.tar.xz', target,
                                 hashlib.sha256(raw).hexdigest(), expected_size=len(raw))
    assert target.read_bytes() == raw and result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert len(calls) == 2 and delays == [0.5]
    assert list(tmp_path.iterdir()) == [target]


def test_permanent_http_failure_keeps_existing_verified_target_without_retry(tmp_path, monkeypatch):
    import urllib.error
    target = tmp_path / 'source.tar.xz'
    target.write_bytes(b'previous verified source')
    calls = []
    def open_url(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 404, 'not found', {}, None)
    monkeypatch.setattr(accounting.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(accounting.time, 'sleep', lambda *args: pytest.fail('HTTP404 must not be retried'))
    with pytest.raises(urllib.error.HTTPError):
        accounting.download('https://official.invalid/source.tar.xz', target, 'a'*64)
    assert target.read_bytes() == b'previous verified source'
    assert len(calls) == 1 and list(tmp_path.iterdir()) == [target]
