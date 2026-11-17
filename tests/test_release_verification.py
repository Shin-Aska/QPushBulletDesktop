"""Release evidence binding and fixture boundaries; no real profiles or binaries."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest


pytestmark = pytest.mark.history_bootstrap
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_verification', ROOT / 'packaging/verify_release.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def accepted():
    measurements = {
        'upgrade.old_binary_reads_fixture': {'actual_old_binary_api_read': True, 'retained_categories': 4},
        'upgrade.same_path_replaced': {'identical_executable_path': True, 'new_bytes_sha256': 'b'*64},
        'upgrade.retained_history': {'notification_and_sms_retained': True},
        'upgrade.retained_settings': {'preferences': {'main_page': 'sms', 'color_scheme': 'dark'}},
        'upgrade.retained_summary': {'same_saved_summary_identity': True, 'content_retained': True},
        'upgrade.retained_credential_reference': {'provider_location_reference_unchanged': True, 'service_credentials_unchanged': True},
        'upgrade.wallet_entry_and_key_unchanged': {'bootstrap_reference_unchanged': True, 'same_key_via_normal_wallet_adapter': True, 'reenrollment': False},
        'recovery.missing_key_stays_locked': {'history_http_status': 423, 'no_automatic_enrollment': True, 'fictional_wallet_keys_erased': 1},
        'recovery.invalid_recovery_rejected': {'malformed_http_status': 409, 'well_formed_wrong_key_http_status': 409, 'bootstrap_unchanged': True},
        'recovery.fresh_entry_same_database_key': {'database_identity_unchanged': True, 'fresh_wallet_entry': True, 'same_key_via_normal_wallet_adapter': True},
        'recovery.data_and_settings_retained': {'retained_categories': 4, 'saved_summary_identity_unchanged': True},
        'recovery.restart_unlocks': {'normal_wallet_unlock': True, 'new_reference_unchanged_on_restart': True, 'retained_categories': 4},
        'migration.requires_explicit_action': {'state': 'plaintext', 'history_http_status': 423, 'implicit_migration': False},
        'migration.interrupted_at_durable_phase': {'phase': 'exporting', 'boundary': 'inotify durable journal rename', 'frozen_processes_stopped': 1},
        'migration.resumed_without_reenrollment': {'same_wallet_reference_after_resume': True, 'journal_removed': True},
        'migration.retained_plaintext_fixture': {'legacy_notification_retained': True, 'legacy_preferences_retained': True},
    }
    for name in ('upgrade.encrypted_database_and_wal', 'recovery.encrypted_database_and_wal', 'migration.encrypted_database_and_wal'):
        measurements[name] = {'database_bytes': 8192, 'wal_bytes': 4096, 'plaintext_sqlite_rejected': True}
    return {'schema_version': 1, 'kind': 'packaged-release-acceptance', 'classification': 'release',
        'relation': 'upgrade', 'status': 'pass',
        'artifacts': {'old': {'sha256': 'a'*64, 'version': '0.1.0', 'source_revision': 'f'*40},
                      'new': {'sha256': 'b'*64, 'source_revision': 'c'*40, 'version': '1.0.0',
                              'runtime_id': 'd'*64, 'appimage_runtime_sha256': 'e'*64,
                              'observed_runtime': {'runtime_id': 'd'*64, 'appimage_runtime_sha256': 'e'*64, 'version': '1.0.0'},
                              'source_materials': {'source_archive_sha256': 'f'*64,
                                  'licenses_archive_sha256': '1'*64,
                                  'corresponding_source_manifest_sha256': '2'*64,
                                  'corresponding_source_archives': {'source-01.tar': '3'*64},
                                  'lockfiles': {'requirements.lock': '4'*64}}}},
        'checks': [{'id': name, 'scope': name.split('.')[0], 'status': 'pass',
                    'evidence': measurements[name]} for name in verifier.REQUIRED_CHECKS]}


def validate(value):
    return verifier.validate_acceptance(value, new_sha256='b'*64, source_revision='c'*40,
        baseline={'sha256': 'a'*64, 'source_revision': 'f'*40, 'version': '0.1.0'})


def test_release_gate_maps_exact_measured_old_to_new_outcomes():
    result = validate(accepted())
    assert result['encrypted_profile_upgrade_verified']
    assert result['accepted_old_sha256'] == 'a'*64
    assert result['required_outcomes'] == 19


@pytest.mark.parametrize('change', ['trial', 'same', 'source', 'image', 'missing', 'failed', 'empty', 'duplicate', 'downgrade', 'materials', 'baseline_sha', 'baseline_source', 'runtime_observation'])
def test_release_gate_rejects_unbound_incomplete_or_same_version_evidence(change):
    report = accepted()
    if change == 'trial':
        report['classification'] = 'trial'
    elif change == 'same':
        report['relation'] = 'same'
    elif change == 'source':
        report['artifacts']['new']['source_revision'] = 'd'*40
    elif change == 'image':
        report['artifacts']['new']['sha256'] = 'e'*64
    elif change == 'missing':
        report['checks'].pop()
    elif change == 'failed':
        report['checks'][0]['status'] = 'fail'
    elif change == 'empty':
        report['checks'][0]['evidence'] = {}
    elif change == 'duplicate':
        report['checks'].append(deepcopy(report['checks'][0]))
    elif change == 'downgrade':
        report['artifacts']['old']['version'] = '2.0.0'
    elif change == 'materials':
        report['artifacts']['new']['source_materials']['corresponding_source_archives'] = {}
    elif change == 'baseline_sha':
        report['artifacts']['old']['sha256'] = '9'*64
    elif change == 'baseline_source':
        report['artifacts']['old']['source_revision'] = '9'*40
    else:
        report['artifacts']['new']['observed_runtime']['version'] = '0.1.0'
    with pytest.raises(ValueError):
        validate(report)


def test_existing_smoke_must_match_exact_binary_and_all_checks(tmp_path):
    path = tmp_path / 'smoke.json'
    value = {'status': 'pass', 'sha256': 'a'*64, 'checks': [{'status': 'pass'} for _ in range(46)]}
    path.write_text(json.dumps(value))
    assert verifier.validate_smoke(path, 'a'*64)['checks'] == 46
    with pytest.raises(ValueError):
        verifier.validate_smoke(path, 'b'*64)
    value['checks'][10]['status'] = 'fail'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verifier.validate_smoke(path, 'a'*64)


@pytest.mark.parametrize('identity,field,value', [
    ('upgrade.same_path_replaced', 'new_bytes_sha256', 'e'*64),
    ('upgrade.wallet_entry_and_key_unchanged', 'reenrollment', True),
    ('recovery.invalid_recovery_rejected', 'well_formed_wrong_key_http_status', 200),
    ('migration.interrupted_at_durable_phase', 'frozen_processes_stopped', 0),
    ('migration.interrupted_at_durable_phase', 'phase', 'switched'),
    ('migration.encrypted_database_and_wal', 'wal_bytes', 0),
])
def test_pass_label_cannot_override_failed_concrete_measurement(identity, field, value):
    report = accepted()
    next(check for check in report['checks'] if check['id'] == identity)['evidence'][field] = value
    with pytest.raises(ValueError):
        validate(report)


def test_private_fixture_environment_clears_inherited_credentials_and_restores(monkeypatch):
    monkeypatch.setenv('PUSHBULLET_TOKEN', 'fictional-inherited-token')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/fictional-inherited-bus')
    before = dict(os.environ)
    with verifier.private_environment({'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/tmp/fictional-owned-bus'}):
        assert os.environ['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/tmp/fictional-owned-bus'
        assert 'PUSHBULLET_TOKEN' not in os.environ
        assert 'PYTHONPATH' not in os.environ
    assert dict(os.environ) == before


def test_process_boundary_refuses_nonowned_and_empty_pid_sets():
    fixture = SimpleNamespace(cached_apps=lambda: [{'pid': 123}, {'pid': 456}])
    assert verifier.owned_pids(fixture, [123]) == {123}
    for pids in ([], [1], [123, 1]):
        with pytest.raises(ValueError, match='Refusing'):
            verifier.owned_pids(fixture, pids)


def test_output_cannot_replace_binary_sidecar_or_existing_proof(tmp_path):
    image = tmp_path / 'immutable.AppImage'
    image.write_bytes(b'immutable fictional binary')
    sidecar = tmp_path / 'source.tar.gz'
    sidecar.write_bytes(b'immutable fictional materials')
    proof = tmp_path / 'old-proof.json'
    proof.write_text('previous proof')
    for output in (image, sidecar, proof):
        with pytest.raises(ValueError, match='new file'):
            verifier.validate_output_path(output, [image])
    alias = tmp_path / 'alias.AppImage'
    alias.symlink_to(image)
    with pytest.raises(ValueError):
        verifier.validate_output_path(alias, [image])
    assert image.read_bytes() == b'immutable fictional binary'
    assert verifier.validate_output_path(tmp_path / 'new-proof.json', [image]) == tmp_path / 'new-proof.json'


def test_cli_output_collision_fails_before_running_or_modifying_input(tmp_path):
    image = tmp_path / 'immutable.AppImage'
    image.write_bytes(b'immutable fictional binary')
    with pytest.raises(SystemExit):
        verifier.main(['--old-image', str(image), '--new-image', str(image),
                       '--output', str(image), '--classification', 'trial'])
    assert image.read_bytes() == b'immutable fictional binary'


def test_wrong_recovery_fixture_is_well_formed_for_correct_database_identity():
    from pbdesk.services.history_security import HistorySecurity
    identity = str(uuid.uuid4())
    parsed, key = HistorySecurity._parse_recovery(verifier.wrong_recovery(identity))
    assert parsed == identity and len(key) == 32


def test_artifact_record_binds_bytes_source_and_immutable_materials(tmp_path):
    image = tmp_path / 'fictional.AppImage'
    image.write_bytes(b'fictional binary bytes')
    source = tmp_path / 'pbdesk-1.0.0-source.tar.gz'
    source.write_bytes(b'fictional source bytes')
    licenses = tmp_path / 'pbdesk-1.0.0-licenses.tar.gz'
    licenses.write_bytes(b'fictional licenses bytes')
    metadata = tmp_path / 'pbdesk-1.0.0-build-info.json'
    metadata.write_text(json.dumps({'source_revision': 'c'*40, 'version': '1.0.0', 'runtime_id': 'd'*64,
                                   'lockfiles': {'requirements.lock': 'e'*64}}))
    record = verifier.artifact_record(image)
    assert record['sha256'] == verifier.sha256(image)
    assert record['source_materials']['source_archive_sha256'] == verifier.sha256(source)
    assert record['source_materials']['licenses_archive_sha256'] == verifier.sha256(licenses)
    # Mutable external build-info is intentionally not hashed into the proof.
    assert 'build_info_sha256' not in record


def test_plaintext_fixture_satisfies_real_schema_and_contains_legacy_history(tmp_path):
    import sqlite3
    verifier.seed_plaintext(SimpleNamespace(settings=SimpleNamespace(application_dir=tmp_path)))
    database = tmp_path / 'pbdesk.sqlite3'
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT body,notification_claimed FROM items').fetchall() == [(verifier.LEGACY, 0)]
        assert conn.execute('SELECT count(*) FROM fictional_boundary_payload').fetchone()[0] == 8
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert database.stat().st_mode & 0o777 == 0o600
