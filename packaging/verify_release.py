#!/usr/bin/env python3
"""Actual AppImage upgrade/recovery/migration acceptance on disposable profiles.

No application module is injected into a frozen process. Source imports are used
only to seed fictional offline fixtures through normal history/wallet adapters.
The actual old binary must read the seed before its same-path replacement.
Output contains artifact identity and measured outcomes, never keys or child logs.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import signal
import sqlite3
import struct
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = 'fictional-release-account'
NOTE = 'Fictional retained release notification 7d40d0c9'
SMS = 'Fictional retained release SMS 10ea4317'
SUMMARY = 'Fictional saved summary **retained** 607027a1'
LEGACY = 'Fictional legacy plaintext history 29e53eab'
REFERENCE = {'backend': 'kwallet', 'wallet': 'fictional-provider-wallet',
             'folder': 'Fictional provider', 'entry': 'fictional-provider-reference'}
PREFERENCES = {'main_page': 'sms', 'color_scheme': 'dark'}
REQUIRED_CHECKS = (
    'upgrade.old_binary_reads_fixture', 'upgrade.same_path_replaced',
    'upgrade.retained_history', 'upgrade.retained_settings', 'upgrade.retained_summary',
    'upgrade.retained_credential_reference', 'upgrade.wallet_entry_and_key_unchanged',
    'upgrade.encrypted_database_and_wal', 'recovery.missing_key_stays_locked',
    'recovery.invalid_recovery_rejected', 'recovery.fresh_entry_same_database_key',
    'recovery.data_and_settings_retained', 'recovery.restart_unlocks',
    'recovery.encrypted_database_and_wal', 'migration.requires_explicit_action',
    'migration.interrupted_at_durable_phase', 'migration.resumed_without_reenrollment',
    'migration.retained_plaintext_fixture', 'migration.encrypted_database_and_wal',
)


def load_sibling(name):
    spec = importlib.util.spec_from_file_location('release_fixture_' + name,
                                                 Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def artifact_record(path):
    path = Path(path).resolve(strict=True)
    candidates = list(path.parent.glob('*-build-info.json'))
    revision, info = None, {}
    if len(candidates) == 1:
        info = json.loads(candidates[0].read_text())
        revision = info['source_revision']
        if not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('Invalid artifact source revision')
    sources = list(path.parent.glob('*-source.tar.gz'))
    licenses = list(path.parent.glob('*-licenses.tar.gz'))
    prefix = 'pbdesk-' + str(info.get('version'))
    ledger = path.parent / (prefix + '-corresponding-source.json')
    archives = sorted(path.parent.glob(prefix + '-corresponding-source-*.tar'))
    return {'name': path.name, 'sha256': sha256(path), 'size_bytes': path.stat().st_size,
            'source_revision': revision, 'version': info.get('version'),
            'runtime_id': info.get('runtime_id'),
            'appimage_runtime_sha256': info.get('appimage_runtime_sha256'),
            'source_materials': {'source_archive_sha256': sha256(sources[0]) if len(sources) == 1 else None,
                'licenses_archive_sha256': sha256(licenses[0]) if len(licenses) == 1 else None,
                'corresponding_source_manifest_sha256': sha256(ledger) if ledger.is_file() else None,
                'corresponding_source_archives': {entry.name: sha256(entry) for entry in archives},
                'lockfiles': info.get('lockfiles', {})}}


def validate_acceptance(report, *, new_sha256, source_revision, baseline):
    """Fail closed when mapping artifact-bound measured outcomes to a gate."""
    if (report.get('schema_version') != 1 or report.get('kind') != 'packaged-release-acceptance'
            or report.get('classification') != 'release' or report.get('status') != 'pass'):
        raise ValueError('Passing release acceptance evidence is required')
    artifacts = report.get('artifacts', {})
    old, new = artifacts.get('old', {}), artifacts.get('new', {})
    if (not isinstance(baseline, dict) or set(baseline) != {'sha256', 'source_revision', 'version'}
            or not re.fullmatch('[0-9a-f]{64}', str(baseline.get('sha256')))
            or not re.fullmatch('[0-9a-f]{40}', str(baseline.get('source_revision')))
            or any(old.get(key) != value for key, value in baseline.items())):
        raise ValueError('Acceptance does not match the independently pinned baseline policy')
    if (not re.fullmatch('[0-9a-f]{64}', str(new_sha256))
            or not re.fullmatch('[0-9a-f]{40}', str(source_revision))
            or new.get('sha256') != new_sha256 or new.get('source_revision') != source_revision
            or not re.fullmatch('[0-9a-f]{64}', str(old.get('sha256')))
            or old['sha256'] == new_sha256 or report.get('relation') != 'upgrade'
            or version_tuple(old.get('version')) >= version_tuple(new.get('version'))):
        raise ValueError('Acceptance evidence does not bind this old/new artifact pair')
    materials = new.get('source_materials', {})
    observed = new.get('observed_runtime', {})
    if any(observed.get(key) != new.get(key) for key in ('runtime_id', 'appimage_runtime_sha256', 'version')):
        raise ValueError('Declared runtime/version differs from actual frozen artifact observations')
    digests = [new.get('runtime_id'), new.get('appimage_runtime_sha256'),
               *(materials.get(name) for name in ('source_archive_sha256', 'licenses_archive_sha256',
                                                   'corresponding_source_manifest_sha256'))]
    archives, lockfiles = materials.get('corresponding_source_archives'), materials.get('lockfiles')
    if (any(not re.fullmatch('[0-9a-f]{64}', str(value)) for value in digests)
            or not isinstance(archives, dict) or not archives or not isinstance(lockfiles, dict) or not lockfiles
            or any(Path(name).name != name or not re.fullmatch('[0-9a-f]{64}', str(value))
                   for name, value in archives.items())
            or any(not re.fullmatch('[0-9a-f]{64}', str(value)) for value in lockfiles.values())):
        raise ValueError('Acceptance evidence lacks immutable runtime/source material bindings')
    checks = report.get('checks', [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise ValueError('Malformed acceptance outcomes')
    ids = [c.get('id') for c in checks]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate acceptance outcomes')
    by_id = {c['id']: c for c in checks}
    if any(key not in by_id or by_id[key].get('status') != 'pass'
           or not isinstance(by_id[key].get('evidence'), dict) or not by_id[key]['evidence']
           for key in REQUIRED_CHECKS) or any(c.get('status') != 'pass' for c in checks):
        raise ValueError('Required measured acceptance outcomes are missing or failed')
    validate_measurements({key: by_id[key]['evidence'] for key in REQUIRED_CHECKS}, new_sha256)
    return {'encrypted_profile_upgrade_verified': True,
            'accepted_new_sha256': new_sha256, 'accepted_source_revision': source_revision,
            'accepted_old_sha256': old['sha256'], 'required_outcomes': len(REQUIRED_CHECKS),
            'accepted_runtime_id': new['runtime_id'],
            'accepted_appimage_runtime_sha256': new['appimage_runtime_sha256'],
            'accepted_source_materials': materials}


def validate_measurements(evidence, image_digest):
    """Check concrete reported observations rather than trusting pass booleans."""
    expected = {
        'upgrade.old_binary_reads_fixture': {'actual_old_binary_api_read': True, 'retained_categories': 4},
        'upgrade.same_path_replaced': {'identical_executable_path': True, 'new_bytes_sha256': image_digest},
        'upgrade.retained_history': {'notification_and_sms_retained': True},
        'upgrade.retained_settings': {'preferences': PREFERENCES},
        'upgrade.retained_summary': {'same_saved_summary_identity': True, 'content_retained': True},
        'upgrade.retained_credential_reference': {'provider_location_reference_unchanged': True, 'service_credentials_unchanged': True},
        'upgrade.wallet_entry_and_key_unchanged': {'bootstrap_reference_unchanged': True, 'same_key_via_normal_wallet_adapter': True, 'reenrollment': False},
        'recovery.missing_key_stays_locked': {'history_http_status': 423, 'no_automatic_enrollment': True, 'fictional_wallet_keys_erased': 1},
        'recovery.invalid_recovery_rejected': {'malformed_http_status': 409, 'well_formed_wrong_key_http_status': 409, 'bootstrap_unchanged': True},
        'recovery.fresh_entry_same_database_key': {'database_identity_unchanged': True, 'fresh_wallet_entry': True, 'same_key_via_normal_wallet_adapter': True},
        'recovery.data_and_settings_retained': {'retained_categories': 4, 'saved_summary_identity_unchanged': True},
        'recovery.restart_unlocks': {'normal_wallet_unlock': True, 'new_reference_unchanged_on_restart': True, 'retained_categories': 4},
        'migration.requires_explicit_action': {'state': 'plaintext', 'history_http_status': 423, 'implicit_migration': False},
        'migration.resumed_without_reenrollment': {'same_wallet_reference_after_resume': True, 'journal_removed': True},
        'migration.retained_plaintext_fixture': {'legacy_notification_retained': True, 'legacy_preferences_retained': True},
    }
    for identity, values in expected.items():
        actual = evidence[identity]
        if any(type(actual.get(key)) is not type(value) or actual[key] != value for key, value in values.items()):
            raise ValueError('Reported acceptance measurement is missing or inconsistent: ' + identity)
    for identity in ('upgrade.encrypted_database_and_wal', 'recovery.encrypted_database_and_wal',
                     'migration.encrypted_database_and_wal'):
        actual = evidence[identity]
        if (type(actual.get('database_bytes')) is not int or actual['database_bytes'] <= 0
                or type(actual.get('wal_bytes')) is not int or actual['wal_bytes'] <= 32
                or actual.get('plaintext_sqlite_rejected') is not True):
            raise ValueError('Populated encrypted database/WAL observations are required')
    boundary = evidence['migration.interrupted_at_durable_phase']
    if (boundary.get('phase') != 'exporting'
            or boundary.get('boundary') != 'inotify durable journal rename'
            or type(boundary.get('frozen_processes_stopped')) is not int or boundary['frozen_processes_stopped'] < 1):
        raise ValueError('An actual owned-process migration boundary observation is required')


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Exact stable artifact versions are required')
    return tuple(map(int, value.split('.')))


def validate_output_path(output, inputs):
    """A new evidence file may never replace an input, material, or old proof."""
    output = Path(output).resolve()
    protected = {Path(path).resolve() for path in inputs if path is not None}
    if output in protected or output.exists():
        raise ValueError('Output must be a new file separate from every input and existing evidence')
    return output


def validate_smoke(path, image_digest):
    value = json.loads(Path(path).read_text())
    checks = value.get('checks', [])
    if (value.get('sha256') != image_digest or value.get('status') != 'pass'
            or len(checks) < 46 or any(c.get('status') != 'pass' for c in checks)):
        raise ValueError('Existing packaged smoke evidence does not match this artifact')
    return {'sha256': image_digest, 'evidence_sha256': sha256(path),
            'status': 'pass', 'checks': len(checks),
            'scope': 'Existing actual-binary process survival, restart, Stop All and generic packaged checks; not rerun'}


def wrong_recovery(identity):
    """A checksum-valid fictional backup with the wrong database key."""
    payload = uuid.UUID(identity).bytes + secrets.token_bytes(32)
    checksum = hashlib.sha256(b'pbdesk-history-recovery-v1\0' + payload).digest()[:8]
    return 'pbdesk-history-v1:' + base64.urlsafe_b64encode(payload + checksum).decode().rstrip('=')


@contextmanager
def private_environment(env):
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def bootstrap(h):
    return json.loads((h.settings.application_dir / '.pbdesk-history.json').read_text())


def wallet_key(h):
    from pbdesk.services.kwallet import read_database_key
    with private_environment(h.env):
        return read_database_key(bootstrap(h)['key_uuid'])


def measure_runtime(h, record):
    """Read actual owned frozen runtime identity/version and outer runtime bytes."""
    runtimes = {entry['runtime'] for entry in h.cached_apps()}
    if len(runtimes) != 1:
        raise ValueError('Expected one owned actual frozen core runtime')
    diagnostics = json.loads(h.run([h.image, '--diagnostics']))
    offset = int(h.run([h.image, '--appimage-offset']).strip())
    if not 4096 <= offset < h.image.stat().st_size:
        raise ValueError('Invalid actual AppImage runtime offset')
    with h.image.open('rb') as stream:
        prefix_digest = hashlib.sha256(stream.read(offset)).hexdigest()
    observed = {'runtime_id': next(iter(runtimes)),
                'version': diagnostics['dependencies']['pbdesk'],
                'appimage_runtime_sha256': prefix_digest}
    for key, value in observed.items():
        if record.get(key) is not None and record[key] != value:
            raise ValueError('Artifact metadata differs from actual frozen runtime observation')
    record['observed_runtime'] = observed
    # The accepted 0.1 bundle predates this metadata field. Its actual prefix is
    # measured directly; its approved full image SHA supplies historical identity.
    if record.get('appimage_runtime_sha256') is None:
        record['appimage_runtime_sha256'] = prefix_digest
    return observed


def seed_retained_fixture(h):
    from pbdesk.configuration import save_provider
    from pbdesk.pushbullet.normalize import normalize
    from pbdesk.services.history_security import HistorySecurity
    with private_environment(h.env):
        store = HistorySecurity(h.settings).open_existing()
        try:
            store.set_setting('pushbullet_account_id', ACCOUNT)
            store.ingest(normalize({'iden': 'fictional-release-note', 'type': 'note',
                'title': 'Fictional release fixture', 'body': NOTE, 'created': 1780000000}), account=ACCOUNT)
            store.save_devices([dict(id='fictional-release-phone', nickname='Fictional release phone',
                type='android', manufacturer='Fictional', model='Fixture', active=True,
                can_push=True, can_sms=True)])
            store.merge_sms_thread(ACCOUNT, 'fictional-release-phone', 'fictional-release-thread', [
                dict(message_id='fictional-release-message', direction='incoming', body=SMS,
                     timestamp=1780000000, addresses=['+12025550123'], name='Alex Example',
                     decrypted=True, image_urls=[], recipients=[])], 'fictional-release-fingerprint')
            save_provider(h.settings, REFERENCE)
            preset = store.summaries.save_preset(ACCOUNT, dict(name='Fictional saved scope',
                sources=['notifications'], device_ids=[], conversations=[], conversation_filter='all',
                window_hours=24, model_override=None))
            store.summaries.save_generation(ACCOUNT, dict(
                canonical_config_hash=store.summaries.canonical_config_hash(preset),
                resolved_model='gpt-6-luna', prompt_version='fictional-offline-fixture-v1',
                input_fingerprint='c' * 64, markdown=SUMMARY,
                generated_at=datetime.now(timezone.utc).isoformat()), preset)
        finally:
            store.close()


def read_fixture(h):
    items = h.request('GET', '/api/v1/items')['items']
    sms = h.request('GET', '/api/v1/sms', params={'device_id': 'fictional-release-phone'})
    configuration = h.request('GET', '/api/v1/configuration')
    history = h.request('GET', '/api/v1/ai/history', params={'account_id': ACCOUNT})
    found = [row for row in history['items'] if row.get('markdown') == SUMMARY]
    # List responses may omit markdown; fetch each retained entry locally.
    if not found:
        found = [h.request('GET', '/api/v1/ai/history/' + row['id'], params={'account_id': ACCOUNT})
                 for row in history['items']]
        found = [row for row in found if row.get('markdown') == SUMMARY]
    return dict(history=any(row.get('body') == NOTE for row in items)
                and any(row.get('body') == SMS for row in sms['messages'])
                and sms.get('account_id') == ACCOUNT,
                settings=configuration['preferences'] == PREFERENCES,
                summary=len(found) == 1, credential_reference=configuration['setup']['source'] == REFERENCE,
                summary_id=found[0]['id'] if len(found) == 1 else None)


def encrypted_evidence(h, smoke, needles):
    # A fully retained startup may perform no writes and leave a zero-length WAL.
    # Exercise ordinary synthetic preference writes, restore the expected value,
    # then require populated ciphertext rather than accepting an absent WAL.
    h.request('PATCH', '/api/v1/configuration/preferences', json={'color_scheme': 'light'})
    h.request('PATCH', '/api/v1/configuration/preferences', json=PREFERENCES)
    return {**smoke.encrypted_file_evidence(h.settings.application_dir / 'pbdesk.sqlite3', needles),
            'wal_generated_by': 'Normal synthetic preference API writes; original preferences restored'}


def owned_pids(h, pids):
    """Only currently descended processes whose executable is our cached binary."""
    owned = {row['pid'] for row in h.cached_apps()}
    selected = set(pids)
    if not selected or not selected <= owned:
        raise ValueError('Refusing to signal a process outside the owned frozen fixture')
    return selected


def interrupt_at_journal(h, smoke):
    """Observe the real durable journal via Linux inotify, then stop owned core.

    No product sleep, monkeypatch, injected module or test environment bypass is
    used. A large fictional legacy table makes the real exporting boundary
    observable. Failure to observe it is a failed/unverified acceptance case.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'Private journal observation unavailable')
    journal = h.settings.application_dir / '.pbdesk-history-migration.json'
    watch = libc.inotify_add_watch(fd, os.fsencode(journal.parent), 0x80 | 0x8)  # MOVED_TO / CLOSE_WRITE
    if watch < 0:
        os.close(fd)
        raise OSError(ctypes.get_errno(), 'Private journal observation unavailable')
    pids = [row['pid'] for row in h.cached_apps()]
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(h.request, 'POST', '/api/v1/history/migrate')
    observed = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and observed is None:
            if select.select([fd], [], [], .1)[0]:
                data = os.read(fd, 65536)
                offset = 0
                while offset + 16 <= len(data):
                    _, _, _, length = struct.unpack_from('iIII', data, offset)
                    name = data[offset+16:offset+16+length].split(b'\0', 1)[0]
                    offset += 16 + length
                    if name == journal.name.encode() and journal.exists():
                        for pid in owned_pids(h, pids):
                            os.kill(pid, signal.SIGSTOP)
                        record = json.loads(journal.read_text())
                        if record.get('phase') != 'exporting':
                            raise smoke.SmokeFailure('Unexpected migration journal phase')
                        observed = dict(phase=record['phase'], boundary='inotify durable journal rename',
                                        frozen_processes_stopped=len(pids))
                        for pid in pids:
                            status = Path(f'/proc/{pid}/status').read_text()
                            if not re.search(r'^State:\s+[Tt]', status, re.MULTILINE):
                                smoke.wait_for(lambda pid=pid: re.search(r'^State:\s+[Tt]',
                                    Path(f'/proc/{pid}/status').read_text(), re.MULTILINE), 'owned core SIGSTOP', timeout=5)
                        for pid in pids:
                            os.kill(pid, signal.SIGKILL)
                        break
            if future.done() and observed is None:
                raise smoke.SmokeFailure('Migration completed before interruption was observed')
        if observed is None:
            raise smoke.SmokeFailure('Durable migration interruption was not observed')
        try:
            future.result(timeout=15)
        except h.httpx.TransportError:
            pass  # Expected: the exact owned core was terminated mid-request.
        return observed
    finally:
        os.close(fd)
        executor.shutdown(wait=True, cancel_futures=True)


def seed_plaintext(h):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    database = h.settings.application_dir / 'pbdesk.sqlite3'
    engine = create_engine('sqlite:///' + str(database))
    config = Config()
    config.set_main_option('script_location', str(ROOT / 'src/pbdesk/storage/migrations'))
    try:
        with engine.begin() as conn:
            config.attributes['connection'] = conn
            command.upgrade(config, 'head')
            values = {'pushbullet_account_id': ACCOUNT, 'configuration:ui': PREFERENCES}
            for key, value in values.items():
                conn.exec_driver_sql('INSERT INTO settings(key,value) VALUES (?,?)', (key, json.dumps(value)))
            conn.exec_driver_sql('INSERT INTO items(account_id,source_id,type,title,body,created_at,fingerprint,notification_claimed) VALUES (?,?,?,?,?,?,?,?)',
                (ACCOUNT, 'fictional-legacy-note', 'note', 'Fictional legacy note', LEGACY,
                 '2026-05-29T00:00:00+00:00', 'b'*64, 0))
            conn.exec_driver_sql('CREATE TABLE fictional_boundary_payload (id INTEGER PRIMARY KEY, content BLOB)')
            for n in range(8):
                conn.exec_driver_sql('INSERT INTO fictional_boundary_payload VALUES (?,?)', (n, b'Fictional migration timing fixture\0' * 65536))
    finally:
        engine.dispose()
    database.chmod(0o600)


class Verification:
    def __init__(self, report, smoke):
        self.report, self.smoke = report, smoke

    def check(self, identity, condition, **evidence):
        self.report['checks'].append({'id': identity, 'scope': identity.split('.')[0],
            'status': 'pass' if condition else 'fail', 'evidence': evidence})
        if not condition:
            raise self.smoke.SmokeFailure('Acceptance case failed: ' + identity)

    def core(self, h):
        child = h.spawn([h.image, '--core', '--demo', '--standalone'])
        self.smoke.wait_for(h.health, 'owned signed core')
        return child

    def stop(self, h, child, label):
        h.stop_all(label)
        child.wait(timeout=15)

    def upgrade_recovery(self, old, new, private):
        runtime = {'checks': []}
        h = self.smoke.Harness(old, private, runtime)
        try:
            h.private_services()
            wallet = h.processes[-1]  # private_services creates its one-key fake wallet last.
            core = self.core(h)
            h.protect(saved=True)
            recovery = h.request('POST', '/api/v1/history/recovery-key')['recovery_key']
            original_record, original_key = bootstrap(h), wallet_key(h)
            h.request('PATCH', '/api/v1/configuration/preferences', json=PREFERENCES)
            credentials_digest = sha256(h.settings.state_dir / 'service-credentials.json')
            self.stop(h, core, 'old_stop_before_offline_fixture')
            seed_retained_fixture(h)
            core = self.core(h)
            self.smoke.wait_for(h.ready, 'old actual binary fixture read')
            old_snapshot = read_fixture(h)
            measure_runtime(h, self.report['artifacts']['old'])
            self.check('upgrade.old_binary_reads_fixture', all(old_snapshot[k] for k in
                ('history', 'settings', 'summary', 'credential_reference')),
                actual_old_binary_api_read=True, retained_categories=4)
            self.stop(h, core, 'old_stop_before_same_path_upgrade')
            old_path = h.image
            replacement = h.image.with_suffix('.replacement')
            shutil.copyfile(new, replacement)
            replacement.chmod(0o755)
            os.replace(replacement, h.image)
            self.check('upgrade.same_path_replaced', h.image == old_path and sha256(h.image) == self.report['artifacts']['new']['sha256'],
                identical_executable_path=True, new_bytes_sha256=sha256(h.image))
            core = self.core(h)
            self.smoke.wait_for(h.ready, 'replacement binary normal wallet unlock')
            measure_runtime(h, self.report['artifacts']['new'])
            retained = read_fixture(h)
            self.check('upgrade.retained_history', retained['history'], notification_and_sms_retained=True)
            self.check('upgrade.retained_settings', retained['settings'], preferences=PREFERENCES)
            self.check('upgrade.retained_summary', retained['summary'] and retained['summary_id'] == old_snapshot['summary_id'],
                same_saved_summary_identity=True, content_retained=True)
            self.check('upgrade.retained_credential_reference', retained['credential_reference'] and
                credentials_digest == sha256(h.settings.state_dir / 'service-credentials.json'),
                provider_location_reference_unchanged=True, service_credentials_unchanged=True)
            self.check('upgrade.wallet_entry_and_key_unchanged', bootstrap(h) == original_record and
                hmac.compare_digest(wallet_key(h), original_key), bootstrap_reference_unchanged=True,
                same_key_via_normal_wallet_adapter=True, reenrollment=False)
            raw = encrypted_evidence(h, self.smoke, [NOTE.encode(), SMS.encode(), SUMMARY.encode()])
            self.check('upgrade.encrypted_database_and_wal', True, **raw)
            self.stop(h, core, 'replacement_stop_before_fixture_key_loss')
            wallet.terminate()
            wallet.wait(timeout=10)
            address = h.env['DBUS_SESSION_BUS_ADDRESS']
            wallet = h.spawn([sys.executable, Path(__file__).with_name('fake_kwallet.py'),
                '--bus-address', address, '--private-root', h.root], stdout=__import__('subprocess').PIPE)
            if not select.select([wallet.stdout], [], [], 10)[0] or wallet.stdout.readline() != b'ready\n':
                raise self.smoke.SmokeFailure('Empty private fictional wallet did not restart')
            core = self.core(h)
            status = h.request('GET', '/api/v1/history/security')
            h.request('GET', '/api/v1/items', expected=423)
            self.check('recovery.missing_key_stays_locked', not status['history_ready'] and
                not status['unlocked'] and bootstrap(h) == original_record,
                history_http_status=423, no_automatic_enrollment=True, fictional_wallet_keys_erased=1)
            before_invalid = (h.settings.application_dir / '.pbdesk-history.json').read_bytes()
            codes = []
            for value in ('malformed-fictional-recovery', wrong_recovery(original_record['uuid'])):
                rejected = h.request('POST', '/api/v1/history/recover', expected=409, json={'recovery_key': value})
                codes.append(rejected['detail']['code'])
            self.check('recovery.invalid_recovery_rejected', codes == ['invalid_recovery', 'invalid_recovery'] and
                (h.settings.application_dir / '.pbdesk-history.json').read_bytes() == before_invalid,
                malformed_http_status=409, well_formed_wrong_key_http_status=409, bootstrap_unchanged=True)
            h.request('POST', '/api/v1/history/recover', json={'recovery_key': recovery})
            recovered_record = bootstrap(h)
            self.check('recovery.fresh_entry_same_database_key', recovered_record['uuid'] == original_record['uuid'] and
                recovered_record['key_uuid'] != original_record['key_uuid'] and hmac.compare_digest(wallet_key(h), original_key),
                database_identity_unchanged=True, fresh_wallet_entry=True, same_key_via_normal_wallet_adapter=True)
            h.request('POST', '/api/v1/history/recovery-offer', json={'saved': True})
            self.smoke.wait_for(h.ready, 'explicit recovered history activation')
            recovered = read_fixture(h)
            self.check('recovery.data_and_settings_retained', all(recovered[k] for k in
                ('history', 'settings', 'summary', 'credential_reference')) and recovered['summary_id'] == old_snapshot['summary_id'],
                retained_categories=4, saved_summary_identity_unchanged=True)
            self.stop(h, core, 'recovered_stop_before_unlock_restart')
            core = self.core(h)
            self.smoke.wait_for(h.ready, 'recovered wallet normal restart unlock')
            restarted = read_fixture(h)
            self.check('recovery.restart_unlocks', bootstrap(h) == recovered_record and all(restarted[k] for k in
                ('history', 'settings', 'summary', 'credential_reference')), normal_wallet_unlock=True,
                new_reference_unchanged_on_restart=True, retained_categories=4)
            raw = encrypted_evidence(h, self.smoke, [NOTE.encode(), SMS.encode(), SUMMARY.encode()])
            self.check('recovery.encrypted_database_and_wal', True, **raw)
            self.stop(h, core, 'final_recovered_fixture_stop_all')
            del recovery, original_key
        finally:
            h.close()
            self.report.setdefault('fixture_runtime_checks', []).extend(runtime['checks'])

    def migration(self, new, private):
        runtime = {'checks': []}
        h = self.smoke.Harness(new, private, runtime)
        try:
            h.private_services()
            seed_plaintext(h)
            core = self.core(h)
            status = h.request('GET', '/api/v1/history/security')
            h.request('GET', '/api/v1/items', expected=423)
            self.check('migration.requires_explicit_action', status['state'] == 'plaintext' and not status['unlocked'],
                state='plaintext', history_http_status=423, implicit_migration=False)
            observed = interrupt_at_journal(h, self.smoke)
            core.wait(timeout=15)
            record = bootstrap(h)
            self.check('migration.interrupted_at_durable_phase', True, **observed)
            core = self.core(h)
            status = h.request('GET', '/api/v1/history/security')
            if status['state'] != 'migration_incomplete':
                raise self.smoke.SmokeFailure('Interrupted journal was not offered for explicit resume')
            h.request('POST', '/api/v1/history/migrate')
            journal_removed = not (h.settings.application_dir / '.pbdesk-history-migration.json').exists()
            self.check('migration.resumed_without_reenrollment', bootstrap(h)['key_uuid'] == record['key_uuid'] and journal_removed,
                same_wallet_reference_after_resume=True, journal_removed=journal_removed)
            recovery = h.request('POST', '/api/v1/history/recovery-key')['recovery_key']
            if not isinstance(recovery, str) or len(recovery) < 32:
                raise self.smoke.SmokeFailure('Migrated recovery backup unavailable')
            del recovery
            h.request('POST', '/api/v1/history/recovery-offer', json={'saved': False})
            self.smoke.wait_for(h.ready, 'resumed migration history activation')
            items = h.request('GET', '/api/v1/items')['items']
            configuration = h.request('GET', '/api/v1/configuration')
            self.check('migration.retained_plaintext_fixture', any(row.get('body') == LEGACY for row in items)
                and configuration['preferences'] == PREFERENCES, legacy_notification_retained=True,
                legacy_preferences_retained=True)
            raw = encrypted_evidence(h, self.smoke, [LEGACY.encode()])
            self.check('migration.encrypted_database_and_wal', True, **raw)
            self.stop(h, core, 'final_migrated_fixture_stop_all')
        finally:
            h.close()
            self.report.setdefault('fixture_runtime_checks', []).extend(runtime['checks'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-image', type=Path, required=True)
    parser.add_argument('--new-image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--classification', choices=('trial', 'release'), required=True)
    parser.add_argument('--smoke-evidence', type=Path)
    parser.add_argument('--baseline-sha256')
    parser.add_argument('--baseline-source-revision')
    parser.add_argument('--baseline-version')
    args = parser.parse_args(argv)
    old, new = args.old_image.resolve(strict=True), args.new_image.resolve(strict=True)
    try:
        output = validate_output_path(args.output, (old, new, args.smoke_evidence))
    except ValueError as exc:
        parser.error(str(exc))
    baseline = dict(sha256=args.baseline_sha256, source_revision=args.baseline_source_revision,
                    version=args.baseline_version)
    report = {'schema_version': 1, 'kind': 'packaged-release-acceptance',
        'classification': args.classification, 'status': 'fail',
        'artifacts': {'old': artifact_record(old), 'new': artifact_record(new)}, 'checks': [],
        'isolation': {'profile': 'disposable synthetic encrypted history', 'wallet': 'private in-memory D-Bus fixture',
                      'frozen_pythonpath': False, 'provider_or_paid_ai_calls': False, 'message_actions': False},
        'untested': ['Native KDE/Wayland-specific integration, real KWallet, tray and notifications',
                     'Real providers, paid AI and sends', 'Normal FUSE launch; actual AppImage extraction mode used'],
        'legal_corresponding_source_readiness': 'Not assessed or claimed'}
    old_version, new_version = (version_tuple(report['artifacts'][role]['version']) for role in ('old', 'new'))
    report['relation'] = 'upgrade' if old_version < new_version else 'same' if old_version == new_version else 'downgrade'
    smoke = load_sibling('smoke')
    def alarm(_sig, _frame):
        raise TimeoutError('Packaged release verification bound exceeded')
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(420)
    try:
        if args.classification == 'release' and (report['artifacts']['old']['sha256'] == report['artifacts']['new']['sha256']
                or report['artifacts']['new']['source_revision'] is None):
            raise ValueError('Release acceptance needs distinct artifacts and recorded source provenance')
        if args.classification == 'release' and (any(value is None for value in baseline.values())
                or any(report['artifacts']['old'].get(key) != value for key, value in baseline.items())):
            raise ValueError('Release acceptance requires the exact independently pinned baseline arguments')
        if args.smoke_evidence:
            report['reused_smoke'] = validate_smoke(args.smoke_evidence, report['artifacts']['new']['sha256'])
        verification = Verification(report, smoke)
        with tempfile.TemporaryDirectory(prefix='pbr-up-') as temporary:
            verification.upgrade_recovery(old, new, Path(temporary))
        with tempfile.TemporaryDirectory(prefix='pbr-mg-') as temporary:
            verification.migration(new, Path(temporary))
        if set(REQUIRED_CHECKS) != {check['id'] for check in report['checks']}:
            raise ValueError('Required acceptance coverage is incomplete')
        report['status'] = 'pass'
        if args.classification == 'release':
            report['gate_mapping'] = validate_acceptance(report,
                new_sha256=report['artifacts']['new']['sha256'],
                source_revision=report['artifacts']['new']['source_revision'], baseline=baseline)
    except Exception as exc:
        report['status'] = 'fail'
        report['failure_diagnostics'] = smoke.exception_diagnostics(exc)
    finally:
        signal.alarm(0)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'classification': args.classification,
                      'checks': len(report['checks']), 'evidence': str(output)}))
    return report['status'] != 'pass'


if __name__ == '__main__':
    raise SystemExit(main())
