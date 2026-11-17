"""Final source/metadata promotion boundaries; no real image, wallet or network."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
import baseline
import finalize_release as finalizer
from source_accounting import digest


@pytest.mark.parametrize('xml', [
    '<testsuite tests="0"/>', '<testsuite tests="2" skipped="2"/>',
    '<testsuite tests="2" failures="1"/>', '<testsuite tests="2" errors="1"/>'])
def test_regression_gate_rejects_empty_skipped_only_or_failed_suite(tmp_path, xml):
    path = tmp_path / 'regression.xml'
    path.write_text(xml)
    with pytest.raises(ValueError, match='regression suite'):
        finalizer.regression_result(path)


def test_regression_gate_records_actual_passed_and_optional_skip_counts(tmp_path):
    path = tmp_path / 'regression.xml'
    path.write_text('<testsuites><testsuite tests="914" failures="0" errors="0" skipped="1"/></testsuites>')
    assert finalizer.regression_result(path) == {'sha256': digest(path), 'tests': 914,
        'failures': 0, 'errors': 0, 'skipped': 1, 'passed': 913}


def source_fixture(tmp_path):
    root = tmp_path / 'checkout'
    (root / 'packaging').mkdir(parents=True)
    (root / 'packaging/REBUILDING.md').write_text('actual rebuild instructions')
    (root / 'packaging/source-policy.json').write_text('{"archives":[]}')
    output = tmp_path / 'assets'
    output.mkdir()
    contents = {'upstream/library.tar.xz': b'original upstream bytes',
                'ubuntu/library.debian.tar.xz': b'exact downstream patch bytes',
                'REBUILDING.md': (root / 'packaging/REBUILDING.md').read_bytes(),
                'source-policy.json': (root / 'packaging/source-policy.json').read_bytes()}
    archive = output / 'source-01.tar'
    def write(members):
        with tarfile.open(archive, 'w') as bundle:
            for name, raw in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                bundle.addfile(info, io.BytesIO(raw))
    write(contents)
    import hashlib
    ledger = {'complete': True, 'blockers': [],
              'source_archives': {archive.name: digest(archive)},
              'upstream_materials': [{'component': 'library', 'file': 'library.tar.xz', 'sha256': hashlib.sha256(contents['upstream/library.tar.xz']).hexdigest()}],
              'ubuntu_packages': [{'corresponding_source': {'files': [{'file': 'library.debian.tar.xz',
                  'sha256': hashlib.sha256(contents['ubuntu/library.debian.tar.xz']).hexdigest()}]}}]}
    return root, output, ledger, archive, contents, write


def test_source_gate_verifies_both_original_and_patched_material(tmp_path):
    root, output, ledger, *_ = source_fixture(tmp_path)
    assert finalizer.verify_source_parts(output, ledger, root) == ledger['source_archives']


@pytest.mark.parametrize('change', ['outer-bytes', 'original-bytes', 'missing-patch', 'false-complete'])
def test_source_complete_boolean_cannot_waive_missing_or_changed_bytes(tmp_path, change):
    root, output, ledger, archive, contents, write = source_fixture(tmp_path)
    if change == 'outer-bytes':
        archive.write_bytes(b'changed archive')
    elif change == 'false-complete':
        ledger['complete'] = False
    else:
        if change == 'original-bytes':
            contents['upstream/library.tar.xz'] = b'changed original'
        else:
            del contents['ubuntu/library.debian.tar.xz']
        write(contents)
        ledger['source_archives'][archive.name] = digest(archive)
    with pytest.raises(ValueError):
        finalizer.verify_source_parts(output, ledger, root)


def initial_policy():
    return {'repository': 'example/app', 'initial_baseline': {'kind': 'initial-candidate',
        'target_version': '1.0.0', 'version': '0.1.0', 'source_revision': 'a'*40,
        'sha256': 'b'*64, 'workflow_run_id': 123, 'artifact_id': 456}}


def test_initial_baseline_is_independently_pinned_and_substitution_fails(monkeypatch):
    monkeypatch.setattr(baseline, 'gh_json', lambda *args: pytest.fail('Initial baseline must use explicit pinned policy'))
    policy = initial_policy()
    selected = baseline.select(policy, '1.0.0')
    assert baseline.validate_selection(policy, selected, '1.0.0') == {
        key: selected[key] for key in ('sha256', 'source_revision', 'version')}
    changed = {**selected, 'sha256': 'c'*64}
    with pytest.raises(ValueError, match='independent release policy'):
        baseline.validate_selection(policy, changed, '1.0.0')


def test_future_baseline_is_latest_published_lower_stable_tag(monkeypatch):
    def release(version, **changes):
        return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False, 'id': version,
                'assets': [{'name': f'pbdesk-{version}-x86_64.AppImage', 'id': 10,
                            'digest': 'sha256:' + 'c'*64}], **changes}
    releases = [release('1.0.0'), release('1.0.2'), release('1.1.0'),
                release('1.0.3', draft=True), release('1.0.4', prerelease=True)]
    def api(endpoint):
        if '/releases?' in endpoint:
            return releases
        assert endpoint.endswith('/git/ref/tags/v1.0.2')
        return {'object': {'type': 'commit', 'sha': 'd'*40}}
    monkeypatch.setattr(baseline, 'gh_json', api)
    selected = baseline.select(initial_policy(), '1.1.0')
    assert selected['version'] == '1.0.2'
    assert selected['source_revision'] == 'd'*40 and selected['sha256'] == 'c'*64


def test_external_promotion_never_modifies_tested_image_and_binds_all_assets(tmp_path, monkeypatch):
    image = tmp_path / 'pbdesk-1.0.0-x86_64.AppImage'
    image.write_bytes(b'immutable tested image fixture')
    before = digest(image)
    info_path = tmp_path / 'pbdesk-1.0.0-build-info.json'
    info_path.write_text(json.dumps({'candidate': True, 'final_release_ready': False, 'source_compliance': {'complete': True}}))
    proof = {'version': '1.0.0', 'status': 'pass', 'appimage_sha256': before}
    # The technical assessment is separately tested above and by the actual CI
    # verifier; this unit check isolates the sidecar-only mutation boundary.
    monkeypatch.setattr(finalizer, 'assess', lambda *args, **kwargs: deepcopy(proof))
    finalizer.finalize(tmp_path, root=ROOT)
    assert digest(image) == before
    assert json.loads(info_path.read_text())['final_release_ready'] is True
    assert finalizer.validate_final_bundle(tmp_path, root=ROOT) == proof
    (tmp_path / 'unbound.txt').write_text('not in checked inventory')
    with pytest.raises(ValueError, match='checksum inventory is incomplete'):
        finalizer.validate_final_bundle(tmp_path, root=ROOT)


def test_same_published_candidate_avoids_expiring_initial_artifact_but_cannot_promote(monkeypatch):
    release = {'tag_name': 'v1.0.0', 'draft': False, 'prerelease': False, 'id': 77,
               'assets': [{'name': 'pbdesk-1.0.0-x86_64.AppImage', 'id': 88, 'digest': 'sha256:'+'e'*64}]}
    def api(endpoint):
        if '/releases?' in endpoint:
            return [release]
        if endpoint.endswith('/git/ref/tags/v1.0.0'):
            return {'object': {'type': 'commit', 'sha': 'd'*40}}
        pytest.fail('A same-version candidate must not read the expired initial artifact')
    monkeypatch.setattr(baseline, 'gh_json', api)
    policy = initial_policy()
    selected = baseline.select(policy, '1.0.0', allow_same=True)
    assert selected['kind'] == 'published-release' and selected['version'] == '1.0.0'
    baseline.validate_selection(policy, selected, '1.0.0', allow_same=True)
    with pytest.raises(ValueError, match='independent release policy'):
        baseline.validate_selection(policy, selected, '1.0.0')


def test_trial_measured_evidence_cannot_satisfy_strict_release_gate():
    helper_spec = importlib.util.spec_from_file_location('release_test_fixture', ROOT / 'tests/test_release_verification.py')
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    report = helper.accepted()
    report['classification'], report['relation'] = 'trial', 'same'
    report['artifacts']['old']['version'] = '1.0.0'
    expected = {'sha256': 'a'*64, 'source_revision': 'f'*40, 'version': '1.0.0'}
    finalizer.validate_trial(report, image_hash='b'*64, revision='c'*40, baseline=expected)
    with pytest.raises(ValueError, match='release acceptance'):
        helper.verifier.validate_acceptance(report, new_sha256='b'*64, source_revision='c'*40, baseline=expected)


def test_trial_sidecars_never_mark_final_ready(tmp_path, monkeypatch):
    (tmp_path / 'pbdesk-1.0.0-x86_64.AppImage').write_bytes(b'candidate image')
    info_path = tmp_path / 'pbdesk-1.0.0-build-info.json'
    info_path.write_text('{"source_compliance":{"complete":true}}')
    def assess(*args, classification, **kwargs):
        if classification == 'release':
            raise ValueError('Trial is not final release acceptance')
        return {'version': '1.0.0', 'classification': 'trial', 'status': 'pass'}
    monkeypatch.setattr(finalizer, 'assess', assess)
    finalizer.finalize(tmp_path, root=ROOT, classification='trial')
    info = json.loads(info_path.read_text())
    assert info['candidate'] is True and info['final_release_ready'] is False
    finalizer.validate_bundle(tmp_path, root=ROOT, classification='trial')
    with pytest.raises(ValueError, match='not final'):
        finalizer.validate_final_bundle(tmp_path, root=ROOT)


def application_tar(path, raw, *, mode=0o644):
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('pbdesk-source/src/application.py')
        member.mode, member.size = mode, len(raw)
        archive.addfile(member, io.BytesIO(raw))


def test_wrong_app_source_supplied_before_acceptance_fails_independent_source_match(tmp_path, monkeypatch):
    import hashlib
    current = {'src/application.py': {'sha256': hashlib.sha256(b'current source').hexdigest(), 'mode': 0o644}}
    monkeypatch.setattr(finalizer, 'tracked_source_inventory', lambda root: current)
    path = tmp_path / 'source.tar.gz'
    application_tar(path, b'old source')
    stale = {'src/application.py': {'sha256': hashlib.sha256(b'old source').hexdigest(), 'mode': 0o644}}
    with pytest.raises(ValueError, match='clean selected source'):
        finalizer.verify_application_archive(path, stale, tmp_path)
    with pytest.raises(ValueError, match='archive bytes differ'):
        finalizer.verify_application_archive(path, current, tmp_path)
    application_tar(path, b'current source', mode=0o755)
    with pytest.raises(ValueError, match='membership/mode'):
        finalizer.verify_application_archive(path, current, tmp_path)
    application_tar(path, b'current source')
    finalizer.verify_application_archive(path, current, tmp_path)


def test_wrong_license_tar_supplied_before_acceptance_fails_sealed_tree_match(tmp_path):
    sealed = tmp_path / 'sealed-notices'
    sealed.mkdir()
    (sealed / 'LICENSE').write_text('old license material')
    path = tmp_path / 'licenses.tar.gz'
    with tarfile.open(path, 'w:gz') as source:
        source.add(sealed, arcname='pbdesk-licenses')
    (sealed / 'LICENSE').write_text('new sealed license material')
    with pytest.raises(ValueError, match='archive bytes differ'):
        finalizer.verify_license_archive(path, sealed)
    with tarfile.open(path, 'w:gz') as source:
        source.add(sealed, arcname='pbdesk-licenses')
    finalizer.verify_license_archive(path, sealed)


def test_self_consistent_external_toolchain_cannot_override_selected_pin(tmp_path):
    (tmp_path / 'packaging').mkdir()
    (tmp_path / 'packaging/toolchain.json').write_text('{"appimage_runtime":{"sha256":"reviewed"}}')
    info = {'toolchain': {'appimage_runtime': {'sha256': 'self-declared'}},
            'appimage_runtime_sha256': 'self-declared'}
    with pytest.raises(ValueError, match='independently reviewed'):
        finalizer.sealed_source_anchor(tmp_path / 'unused-image', info, {}, 'unused', tmp_path, tmp_path / 'unused-licenses')


def test_partial_lock_mapping_cannot_waive_required_build_runtime_and_test_locks(tmp_path):
    for name in ('requirements-build.lock', 'packaging/requirements-runtime.lock', 'packaging/requirements-test.lock'):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(name)
    complete = finalizer.expected_lockfiles(tmp_path)
    assert finalizer.verified_lockfiles(tmp_path, complete) == complete
    with pytest.raises(ValueError, match='Complete mandatory'):
        finalizer.verified_lockfiles(tmp_path, {'requirements-build.lock': complete['requirements-build.lock']})


@pytest.mark.parametrize('alias', ['symlink', 'hardlink'])
def test_output_sidecar_alias_never_mutates_image(tmp_path, monkeypatch, alias):
    image = tmp_path / 'pbdesk-1.0.0-x86_64.AppImage'
    image.write_bytes(b'immutable image despite alias')
    before = digest(image)
    for name in ('pbdesk-1.0.0-readiness.json', 'pbdesk-1.0.0-SHA256SUMS'):
        target = tmp_path / name
        if alias == 'symlink':
            target.symlink_to(image)
        else:
            target.hardlink_to(image)
    (tmp_path / 'pbdesk-1.0.0-build-info.json').write_text('{"source_compliance":{}}')
    monkeypatch.setattr(finalizer, 'assess', lambda *args, **kwargs: {'version': '1.0.0', 'status': 'pass'})
    finalizer.finalize(tmp_path, root=ROOT)
    assert digest(image) == before
    assert not (tmp_path / 'pbdesk-1.0.0-readiness.json').is_symlink()
    finalizer.validate_final_bundle(tmp_path, root=ROOT)
