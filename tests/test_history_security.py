"""Real SQLCipher acceptance tests; all histories and wallets are temporary."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from pbdesk.config import Settings
from pbdesk.services import history_security as security_module, kwallet
from pbdesk.services.history_security import BOOTSTRAP, JOURNAL, MARKER, HistorySecurity, HistorySecurityError
from pbdesk.storage import history_migration
from pbdesk.storage.cipher import connect
from pbdesk.storage.database import Store

pytestmark = pytest.mark.history_bootstrap


@pytest.fixture
def wallet(monkeypatch):
    values, calls = {}, []
    def authorize(selected=None):
        calls.append('authorize')
        return selected or 'fictional-existing-wallet'
    def write(identity, key, selected):
        assert calls and calls[-1] == 'authorize'
        assert identity not in values and len(key) == 32
        values[identity] = key
        calls.append('write')
    def read(identity, *, interactive=False):
        calls.append('read')
        if identity not in values:
            raise kwallet.KWalletUnavailable('fixture missing key')
        return values[identity]
    monkeypatch.setattr(kwallet, 'authorize_database_wallet', authorize)
    monkeypatch.setattr(kwallet, 'write_database_key', write)
    monkeypatch.setattr(kwallet, 'read_database_key', read)
    return values, calls


def plaintext(path, revision='head', *, all_entities=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine('sqlite:///' + str(path))
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[1] / 'src/pbdesk/storage/migrations'))
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, revision)
        if all_entities:
            names = ['items', 'events', 'settings', 'devices', 'actions', 'action_audit', 'locked_envelopes',
                'sms_messages', 'sms_sync', 'sms_threads', 'sms_envelopes', 'device_encryption']
            for table in names:
                values = {}
                for column in inspect(conn).get_columns(table):
                    name, kind = column['name'], str(column['type'])
                    values[name] = 1 if ('INT' in kind or 'FLOAT' in kind) else ('"fixture-value"' if table == 'settings' and name == 'value' else 'fixture-' + name)
                    if name in ('addresses', 'image_urls', 'recipients'):
                        values[name] = '["fixture"]'
                columns = ','.join('"' + name + '"' for name in values)
                conn.exec_driver_sql(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
        conn.exec_driver_sql("INSERT INTO settings(key,value) VALUES ('fixture_preserved','true')")
        conn.exec_driver_sql('PRAGMA user_version=42')
    engine.dispose()


def test_no_implicit_enrollment_or_history_reads(tmp_path, wallet):
    settings = Settings(tmp_path)
    security = HistorySecurity(settings)
    assert security.inspect()['state'] == 'new'
    assert wallet[1] == []
    with pytest.raises(HistorySecurityError):
        Store(settings)
    assert wallet[1] == [] and not security.path.exists()


def test_real_cipher_pool_threads_fts_wal_and_no_plaintext(tmp_path, wallet):
    security = HistorySecurity(Settings(tmp_path))
    store = security.protect()
    try:
        assert security.inspect()['recovery_offer_pending']
        assert wallet[1][:2] == ['authorize', 'write']
        def write(index):
            store.set_setting('thread:' + str(index), {'content': 'private-fixture-value'})
            with store.engine.connect() as conn:
                assert conn.exec_driver_sql('PRAGMA cipher_version').scalar()
                assert conn.exec_driver_sql('PRAGMA temp_store').scalar() == 2
                assert conn.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
                assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
                conn.exec_driver_sql('CREATE VIRTUAL TABLE temp.test_cipher_fts USING fts5(value)')
                conn.exec_driver_sql('DROP TABLE temp.test_cipher_fts')
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(write, range(36)))
        for path in tmp_path.glob('pbdesk.sqlite3*'):
            assert b'private-fixture-value' not in path.read_bytes()
        with sqlite3.connect(store.path) as plain:
            with pytest.raises(sqlite3.DatabaseError):
                plain.execute('SELECT * FROM settings').fetchall()
        with pytest.raises(RuntimeError):
            connect(store.path, secrets.token_bytes(32))
        assert 'private-fixture-value' not in json.dumps(security.inspect())
    finally:
        store.close()


def test_locked_missing_key_and_metadata_never_generate_or_replace(tmp_path, wallet):
    settings = Settings(tmp_path)
    security = HistorySecurity(settings)
    store = security.protect()
    store.set_setting('fixture_preserved', True)
    store.close()
    contents = security.path.read_bytes()
    wallet[0].clear()
    assert HistorySecurity(settings).inspect()['state'] == 'locked'
    with pytest.raises(HistorySecurityError) as error:
        HistorySecurity(settings).open_existing()
    assert error.value.code == 'locked'
    assert security.path.read_bytes() == contents
    (tmp_path / BOOTSTRAP).unlink()
    assert HistorySecurity(settings).inspect()['state'] == 'recovery_required'
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).protect()
    assert wallet[1].count('write') == 1


def test_recovery_validates_before_wallet_mutation_and_backups_remain_valid(tmp_path, wallet):
    settings = Settings(tmp_path)
    security = HistorySecurity(settings)
    store = security.protect()
    backup = security.recovery_key()
    first = json.loads((tmp_path / BOOTSTRAP).read_text())
    store.set_setting('fixture_preserved', True)
    store.close()
    wallet[0].clear()
    (tmp_path / BOOTSTRAP).unlink()
    before = list(wallet[1])
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).recover(backup[:-2] + 'AA')
    assert wallet[1] == before and not (tmp_path / BOOTSTRAP).exists()
    security = HistorySecurity(settings)
    store = security.recover(backup)
    assert store.get_setting('fixture_preserved')
    second = json.loads((tmp_path / BOOTSTRAP).read_text())
    assert second['uuid'] == first['uuid'] and second['key_uuid'] != first['key_uuid']
    assert security.recovery_key() == backup
    assert security.inspect()['recovery_offer_pending']
    security.finish_recovery_offer(store, saved=False)
    store.close()
    wallet[0].clear()
    store = HistorySecurity(settings).recover(backup)
    assert store.get_setting('fixture_preserved')
    store.close()


def test_mismatched_bootstrap_fails_before_store_upgrade(tmp_path, wallet):
    security = HistorySecurity(Settings(tmp_path))
    store = security.protect()
    store.close()
    record = json.loads((tmp_path / BOOTSTRAP).read_text())
    import uuid
    record['uuid'] = str(uuid.uuid4())
    security_module._write_record(tmp_path / BOOTSTRAP, record)
    before = security.path.read_bytes()
    with pytest.raises(HistorySecurityError):
        HistorySecurity(security.settings).open_existing()
    assert before == security.path.read_bytes()


class SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize('boundary', ['record', 'database', 'marker'])
def test_protect_resumes_interrupted_initialization_with_same_key(tmp_path, wallet, monkeypatch, boundary):
    settings = Settings(tmp_path)
    original_write = security_module._write_record
    original_open = Store._open_database
    original_setting = Store.set_setting
    def write(path, value):
        original_write(path, value)
        if boundary == 'record' and value.get('phase') == 'initializing':
            raise SimulatedCrash()
    def opened(self):
        original_open(self)
        if boundary == 'database':
            raise SimulatedCrash()
    def setting(self, name, value):
        original_setting(self, name, value)
        if boundary == 'marker' and name == MARKER:
            raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(security_module, '_write_record', write)
        patch.setattr(Store, '_open_database', opened)
        patch.setattr(Store, 'set_setting', setting)
        with pytest.raises(SimulatedCrash):
            HistorySecurity(settings).protect()
    assert HistorySecurity(settings).inspect()['state'] == 'new'
    security = HistorySecurity(settings)
    store = security.protect()
    assert wallet[1].count('write') == 1
    assert security.inspect()['recovery_offer_pending']
    store.close()


@pytest.mark.parametrize('phase', ['exporting', 'verified', 'switched'])
def test_migration_preserves_all_old_entities_and_resumes_each_phase(tmp_path, wallet, monkeypatch, phase):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    source = settings.state_dir / 'pbdesk.sqlite3'
    plaintext(source, revision='0006', all_entities=True)
    with sqlite3.connect(source) as conn:
        before = history_migration.logical_digest(conn)
    cache = settings.state_dir / 'ai-digest-cache.json'
    cache.write_text(json.dumps({'entries': [], 'fixture': 'private-cache-value'}))
    original = history_migration._journal
    def checkpoint(settings, identity, next_phase):
        original(settings, identity, next_phase)
        if next_phase == phase:
            raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(history_migration, '_journal', checkpoint)
        with pytest.raises(SimulatedCrash):
            HistorySecurity(settings).migrate()
    assert HistorySecurity(settings).inspect()['state'] == 'migration_incomplete'
    security = HistorySecurity(settings)
    store = security.migrate()
    try:
        assert store.get_setting('fixture_preserved')
        assert store.get_setting('ai_runtime:ai-digest-cache.json')['fixture'] == 'private-cache-value'
        assert not source.exists() and not cache.exists()
        assert not (settings.application_dir / JOURNAL).exists()
        with store.engine.connect() as conn:
            assert conn.exec_driver_sql('PRAGMA user_version').scalar() == 42
            assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == '0007'
            assert conn.exec_driver_sql('SELECT payload FROM sms_envelopes').scalar() == 'fixture-payload'
        archives = list(settings.application_dir.glob('*.archive'))
        assert len(archives) == 1
        key = next(iter(wallet[0].values()))
        conn = connect(archives[0], key, readonly=True)
        try:
            assert history_migration.logical_digest(conn, exclude_tables=(history_migration.ARCHIVE_TABLE,)) == before
        finally:
            conn.close()
        assert wallet[1].count('write') == 1
    finally:
        store.close()


def test_plaintext_is_never_opened_by_runtime_or_configuration(tmp_path, wallet):
    from pbdesk.configuration import get
    plaintext(tmp_path / 'pbdesk.sqlite3')
    settings = Settings(tmp_path)
    assert HistorySecurity(settings).inspect()['state'] == 'plaintext'
    with pytest.raises(HistorySecurityError):
        Store(settings)
    with pytest.raises(HistorySecurityError):
        get(settings, 'ui')
    assert wallet[1] == []


def test_migration_conflict_and_live_ownership_preserve_plaintext(tmp_path, wallet):
    import fcntl
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    plaintext(settings.state_dir / 'pbdesk.sqlite3')
    plaintext(settings.application_dir / 'pbdesk.sqlite3')
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).migrate()
    assert wallet[1] == []
    (settings.application_dir / 'pbdesk.sqlite3').unlink()
    fd = os.open(settings.state_dir / '.pbdesk-database.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(HistorySecurityError):
            HistorySecurity(settings).migrate()
        assert wallet[1] == []
    finally:
        os.close(fd)


def test_uncheckpointed_wal_and_external_writer_are_preserved_or_blocked(tmp_path, wallet, monkeypatch):
    import subprocess
    import sys
    settings = Settings(tmp_path)
    source = tmp_path / 'pbdesk.sqlite3'
    plaintext(source)
    # A hard-exited old process leaves committed rows only in the WAL.
    script = "import sqlite3,sys,os;c=sqlite3.connect(sys.argv[1]);c.execute('pragma journal_mode=wal');c.execute('pragma wal_autocheckpoint=0');c.execute(\"insert into settings values ('wal_only','true')\");c.commit();os._exit(0)"
    subprocess.run([sys.executable, '-c', script, str(source)], check=True)
    assert Path(str(source) + '-wal').stat().st_size > 0
    original = history_migration._archive_snapshot
    def archive(*args, **kwargs):
        writer_script = "import sqlite3,sys;c=sqlite3.connect(sys.argv[1],timeout=.01)\ntry:c.execute(\"INSERT INTO settings VALUES ('racing_write','true')\");c.commit()\nexcept sqlite3.OperationalError:sys.exit(0)\nsys.exit(1)"
        result = subprocess.run([sys.executable, '-c', writer_script, str(source)], capture_output=True)
        assert result.returncode == 0, 'An external source writer must remain blocked during verification/cutover'
        return original(*args, **kwargs)
    monkeypatch.setattr(history_migration, '_archive_snapshot', archive)
    store = HistorySecurity(settings).migrate()
    try:
        assert store.get_setting('wal_only')
        assert store.get_setting('racing_write') is None
    finally:
        store.close()


def test_retained_unchanged_ancestor_is_verified_and_both_snapshots_encrypted(tmp_path, wallet):
    import hashlib
    import shutil
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    ancestor = settings.state_dir / 'pbdesk.sqlite3'
    target = settings.application_dir / 'pbdesk.sqlite3'
    plaintext(ancestor)
    settings.application_dir.mkdir()
    shutil.copyfile(ancestor, target)
    with sqlite3.connect(ancestor) as conn:
        digest = hashlib.sha256()
        for line in conn.iterdump():
            digest.update(line.encode())
    with sqlite3.connect(target) as conn:
        conn.execute('INSERT INTO settings VALUES (?,?)', ('database_legacy_source', json.dumps({'path': str(ancestor.resolve()), 'digest': digest.hexdigest()})))
        conn.execute("INSERT INTO settings VALUES ('newer_application_history','true')")
    security = HistorySecurity(settings)
    assert security.inspect()['state'] == 'plaintext'
    store = security.migrate()
    try:
        assert store.get_setting('newer_application_history')
        assert not ancestor.exists()
        archives = list(settings.application_dir.glob('*.archive'))
        assert len(archives) == 2
        for archive in archives:
            with sqlite3.connect(archive) as conn:
                with pytest.raises(sqlite3.DatabaseError):
                    conn.execute('SELECT * FROM settings').fetchall()
    finally:
        store.close()


@pytest.mark.parametrize('database_present', [True, False])
def test_migration_resumes_metadata_before_journal_and_partial_file_cleanup(tmp_path, wallet, monkeypatch, database_present):
    settings = Settings(tmp_path)
    if database_present:
        plaintext(tmp_path / 'pbdesk.sqlite3')
    first, second = tmp_path / 'ai-digest-cache.json', tmp_path / 'ai-runtime-state.json'
    for path in (first, second):
        path.write_text('{"entries":[]}')
    original_write = security_module._write_record
    def write(path, value):
        original_write(path, value)
        if value.get('phase') == 'migrating':
            raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(security_module, '_write_record', write)
        with pytest.raises(SimulatedCrash):
            HistorySecurity(settings).migrate()
    assert not (tmp_path / JOURNAL).exists()
    assert HistorySecurity(settings).inspect()['state'] == 'plaintext'
    def cleanup(artifacts):
        history_migration._cleanup_artifacts_original(artifacts[:1])
        raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(history_migration, '_cleanup_artifacts_original', history_migration._cleanup_artifacts, raising=False)
        patch.setattr(history_migration, '_cleanup_artifacts', cleanup)
        with pytest.raises(SimulatedCrash):
            HistorySecurity(settings).migrate()
    assert (tmp_path / JOURNAL).exists()
    store = HistorySecurity(settings).migrate()
    if database_present:
        assert store.get_setting('fixture_preserved')
    assert store.get_setting('ai_runtime:ai-digest-cache.json') == {'entries': []}
    assert not first.exists() and not second.exists()
    assert wallet[1].count('write') == 1
    store.close()


def test_inspection_does_not_read_open_history_or_wallet(tmp_path, wallet, monkeypatch):
    security = HistorySecurity(Settings(tmp_path))
    store = security.protect()
    monkeypatch.setattr(store, 'get_setting', lambda *args: pytest.fail('Inspection must not query history'))
    before = list(wallet[1])
    assert security.inspect()['state'] == 'ready'
    assert security.inspect()['recovery_offer_pending']
    assert wallet[1] == before
    store.close()


@pytest.mark.parametrize('phase', [None, 'bogus', 'migrating'])
def test_wrong_bootstrap_phase_never_opens_or_upgrades_history(tmp_path, wallet, phase):
    security = HistorySecurity(Settings(tmp_path))
    store = security.protect()
    store.close()
    path = tmp_path / BOOTSTRAP
    record = json.loads(path.read_text())
    if phase is None:
        record.pop('phase')
    else:
        record['phase'] = phase
    security_module._write_record(path, record)
    before, calls = security.path.read_bytes(), list(wallet[1])
    assert HistorySecurity(security.settings).inspect()['state'] == 'recovery_required'
    with pytest.raises(HistorySecurityError):
        HistorySecurity(security.settings).open_existing()
    with pytest.raises(HistorySecurityError):
        Store(security.settings)
    assert wallet[1] == calls and security.path.read_bytes() == before


def test_app_directory_configuration_imports_despite_old_import_marker(tmp_path, wallet):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    plaintext(settings.application_dir / 'pbdesk.sqlite3')
    with sqlite3.connect(settings.application_dir / 'pbdesk.sqlite3') as conn:
        conn.execute("INSERT INTO settings VALUES ('configuration:import_v1','true')")
    values = {'provider-credential.json': {'backend': 'kwallet', 'wallet': 'fictional', 'folder': 'Pushbullet', 'entry': 'api-key'},
        'ai-settings.json': {'model': 'gpt-6-luna', 'configured': True}, 'ui-preferences.json': {'color_scheme': 'dark'}}
    for filename, value in values.items():
        (settings.application_dir / filename).write_text(json.dumps(value))
    store = HistorySecurity(settings).migrate()
    try:
        assert store.get_setting('configuration:provider')['wallet'] == 'fictional'
        assert store.get_setting('configuration:ai')['configured']
        assert store.get_setting('configuration:ui')['color_scheme'] == 'dark'
        assert all(not (settings.application_dir / filename).exists() for filename in values)
    finally:
        store.close()


def test_conflicting_selected_runtime_caches_preserve_both_before_enrollment(tmp_path, wallet):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    plaintext(settings.application_dir / 'pbdesk.sqlite3')
    settings.state_dir.mkdir()
    for index, root in enumerate((settings.application_dir, settings.state_dir)):
        (root / 'ai-runtime-state.json').write_text(json.dumps({'entries': [], 'last_attempt': index}))
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).migrate()
    assert wallet[1] == []
    assert all((root / 'ai-runtime-state.json').exists() for root in (settings.application_dir, settings.state_dir))


def test_relative_state_directory_uses_one_physical_ownership_lock(tmp_path, wallet, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(Path('relative-state'))
    security = HistorySecurity(settings)
    store = security.protect()
    backup = security.recovery_key()
    assert len(history_migration.selected_roots(settings)) == 1
    store.close()
    store = HistorySecurity(settings).recover(backup)
    store.close()


def test_json_only_premigration_retry_preserves_unjournaled_ciphertext(tmp_path, wallet):
    import uuid
    identity = str(uuid.uuid4())
    settings = Settings(tmp_path)
    security_module._write_record(tmp_path / BOOTSTRAP,
        {'version': 1, 'uuid': identity, 'key_uuid': identity, 'phase': 'migrating'})
    (tmp_path / 'ai-runtime-state.json').write_text('{"entries":[]}')
    stage = tmp_path / ('.pbdesk-history-' + identity + '.stage')
    stage.write_bytes(secrets.token_bytes(256))
    before = stage.read_bytes()
    assert HistorySecurity(settings).inspect()['state'] == 'recovery_required'
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).migrate()
    assert stage.read_bytes() == before and wallet[1] == []


@pytest.mark.parametrize('filename,payload,setting', [
    ('ui-preferences.json', {'color_scheme': 'dark'}, 'configuration:ui'),
    ('ai-digest-cache.json', {'entries': []}, 'ai_runtime:ai-digest-cache.json'),
])
@pytest.mark.parametrize('conflicting_retry', [False, True])
def test_json_only_prejournal_retry_validates_duplicate_selected_files(tmp_path, wallet, monkeypatch,
        filename, payload, setting, conflicting_retry):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    selected = [root / filename for root in (settings.application_dir, settings.state_dir)]
    for path in selected:
        path.parent.mkdir()
        path.write_text(json.dumps(payload))
    original_write = security_module._write_record
    def write(path, value):
        original_write(path, value)
        if value.get('phase') == 'migrating':
            raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(security_module, '_write_record', write)
        with pytest.raises(SimulatedCrash):
            HistorySecurity(settings).migrate()
    bootstrap = (settings.application_dir / BOOTSTRAP).read_bytes()
    assert not (settings.application_dir / JOURNAL).exists()
    assert not (settings.application_dir / 'pbdesk.sqlite3').exists()
    if conflicting_retry:
        selected[0].write_text(json.dumps({**payload, 'fixture_conflict': True}))
    preserved = [path.read_bytes() for path in selected]
    security = HistorySecurity(settings)
    assert security.inspect()['state'] == 'plaintext'
    if conflicting_retry:
        with pytest.raises(HistorySecurityError):
            security.migrate()
        assert [path.read_bytes() for path in selected] == preserved
        assert (settings.application_dir / BOOTSTRAP).read_bytes() == bootstrap
        assert not (settings.application_dir / JOURNAL).exists()
        assert not (settings.application_dir / 'pbdesk.sqlite3').exists()
    else:
        store = security.migrate()
        try:
            assert store.get_setting(setting) == payload
            assert all(not path.exists() for path in selected)
            assert security.inspect()['recovery_offer_pending']
        finally:
            store.close()
    assert wallet[1].count('write') == 1


def _filesystem_entry(path):
    import stat
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if path.is_symlink():
        content = os.readlink(path)
    elif stat.S_ISREG(info.st_mode):
        content = path.read_bytes()
    else:
        content = None
    return stat.S_IFMT(info.st_mode), info.st_ino, content


def _invalid_entry(path, kind, outside):
    if os.path.lexists(path):
        path.unlink()
    if kind == 'regular':
        path.write_bytes(b'fictional residual data')
    elif kind == 'dangling':
        path.symlink_to(outside / 'absent')
    elif kind == 'symlink':
        path.symlink_to(outside / 'existing')
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        raise AssertionError(kind)


_INVALID_NAMED_CASES = (
    [('app', name, kind) for name in (BOOTSTRAP, JOURNAL)
        for kind in ('regular', 'dangling', 'symlink', 'directory', 'fifo')]
    + [(root, name, kind) for root in ('app', 'state')
        for name in ('pbdesk.sqlite3', 'pbdesk.sqlite3-wal', 'pbdesk.sqlite3-shm', 'pbdesk.sqlite3-journal')
        for kind in ('regular', 'dangling', 'symlink', 'directory', 'fifo')]
    + [(root, name, kind) for root in ('app', 'state') for name in history_migration.ARTIFACTS
        for kind in ('dangling', 'symlink', 'directory', 'fifo')]
    + [('app', ending + suffix, kind) for ending in ('.stage', '.archive', '.ancestor.archive')
        for suffix in ('', '-wal', '-shm', '-journal')
        for kind in ('regular', 'dangling', 'symlink', 'directory', 'fifo')]
)


@pytest.mark.parametrize('root,name,kind', _INVALID_NAMED_CASES)
def test_named_migration_entries_fail_closed_without_wallet_or_filesystem_mutation(tmp_path, wallet, root, name, kind):
    import uuid
    identity = str(uuid.uuid4())
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    for directory in (settings.application_dir, settings.state_dir, tmp_path / 'outside'):
        directory.mkdir()
    outside = tmp_path / 'outside'
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    security_module._write_record(settings.application_dir / BOOTSTRAP,
        {'version': 1, 'uuid': identity, 'key_uuid': identity, 'phase': 'migrating'})
    (settings.application_dir / 'ui-preferences.json').write_text('{"color_scheme":"dark"}')
    if name.startswith('.') and name not in (BOOTSTRAP, JOURNAL):
        name = '.pbdesk-history-' + identity + name
    path = (settings.application_dir if root == 'app' else settings.state_dir) / name
    _invalid_entry(path, kind, outside)
    before, external = _filesystem_entry(path), _filesystem_entry(outside / 'existing')
    assert HistorySecurity(settings).inspect()['state'] in {'recovery_required', 'conflict'}
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).migrate()
    assert wallet[1] == []
    assert _filesystem_entry(path) == before
    assert _filesystem_entry(outside / 'existing') == external
    assert not (outside / 'absent').exists()
    assert not (settings.application_dir / 'pbdesk.sqlite3').exists() or path.name == 'pbdesk.sqlite3'


@pytest.mark.parametrize('phase', ['exporting', 'verified', 'switched'])
@pytest.mark.parametrize('ending', ['.stage', '.archive', '.ancestor.archive'])
@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
@pytest.mark.parametrize('kind', ['dangling', 'symlink'])
def test_every_journal_phase_rejects_linked_migration_files_before_wallet(tmp_path, wallet, phase, ending, suffix, kind):
    import uuid
    identity = str(uuid.uuid4())
    settings = Settings(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    security_module._write_record(tmp_path / BOOTSTRAP,
        {'version': 1, 'uuid': identity, 'key_uuid': identity, 'phase': 'migrating'})
    history_migration._journal(settings, identity, phase)
    (tmp_path / 'ui-preferences.json').write_text('{}')
    path = tmp_path / ('.pbdesk-history-' + identity + ending + suffix)
    _invalid_entry(path, kind, outside)
    before = _filesystem_entry(path)
    assert HistorySecurity(settings).inspect()['state'] == 'recovery_required'
    with pytest.raises(HistorySecurityError):
        HistorySecurity(settings).migrate()
    assert wallet[1] == [] and _filesystem_entry(path) == before
    assert (outside / 'existing').read_bytes() == b'outside fixture must remain unchanged'
    assert not (outside / 'absent').exists()


@pytest.mark.parametrize('kind', ['regular', 'dangling', 'symlink', 'directory', 'fifo'])
def test_encrypted_archive_creation_never_replaces_or_follows_an_existing_entry(tmp_path, kind):
    source, destination, outside = tmp_path / 'source', tmp_path / 'archive', tmp_path / 'outside'
    outside.mkdir()
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    key = secrets.token_bytes(32)
    conn = connect(source, key)
    conn.execute('CREATE TABLE fixture(value TEXT)')
    conn.execute("INSERT INTO fixture VALUES ('encrypted fixture')")
    conn.commit()
    history_migration._checkpoint(conn)
    conn.close()
    _invalid_entry(destination, kind, outside)
    before, original = _filesystem_entry(destination), _filesystem_entry(source)
    with pytest.raises((OSError, RuntimeError)):
        history_migration._copy_encrypted(source, destination)
    assert _filesystem_entry(destination) == before and _filesystem_entry(source) == original
    assert (outside / 'existing').read_bytes() == b'outside fixture must remain unchanged'
    assert not (outside / 'absent').exists()


@pytest.mark.parametrize('kind', ['dangling', 'symlink', 'directory', 'fifo'])
@pytest.mark.parametrize('readonly', [False, True])
def test_cipher_checks_original_entry_before_driver_open_or_path_resolution(tmp_path, monkeypatch, kind, readonly):
    from pbdesk.storage import cipher
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    path = tmp_path / 'database'
    _invalid_entry(path, kind, outside)
    before = _filesystem_entry(path)
    monkeypatch.setattr(cipher.driver(), 'connect', lambda *args, **kwargs: pytest.fail('Invalid original path reached the SQLCipher driver'))
    with pytest.raises(cipher.CipherUnavailable):
        cipher.connect(path, secrets.token_bytes(32), readonly=readonly)
    assert _filesystem_entry(path) == before
    assert (outside / 'existing').read_bytes() == b'outside fixture must remain unchanged'
    assert not (outside / 'absent').exists()


@pytest.mark.parametrize('kind', ['dangling', 'symlink'])
@pytest.mark.parametrize('name', [BOOTSTRAP, JOURNAL])
def test_opaque_record_writes_preserve_existing_links(tmp_path, kind, name):
    import uuid
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    path = tmp_path / name
    _invalid_entry(path, kind, outside)
    before = _filesystem_entry(path)
    with pytest.raises(RuntimeError):
        security_module._write_record(path, {'version': 1, 'uuid': str(uuid.uuid4())})
    assert _filesystem_entry(path) == before and not list(tmp_path.glob('.history-record-*'))
    assert (outside / 'existing').read_bytes() == b'outside fixture must remain unchanged'
    assert not (outside / 'absent').exists()


@pytest.mark.parametrize('kind', ['dangling', 'symlink'])
@pytest.mark.parametrize('entry', ['legacy_database', 'journal', 'archive_sidecar'])
def test_offline_store_rejects_linked_named_entries_before_wallet_read(tmp_path, wallet, kind, entry):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    security = HistorySecurity(settings)
    store = security.protect()
    identity = store.get_setting(MARKER)['uuid']
    store.close()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'existing').write_bytes(b'outside fixture must remain unchanged')
    paths = {'legacy_database': settings.state_dir / 'pbdesk.sqlite3',
        'journal': settings.application_dir / JOURNAL,
        'archive_sidecar': settings.application_dir / ('.pbdesk-history-' + identity + '.archive-wal')}
    path = paths[entry]
    _invalid_entry(path, kind, outside)
    calls, before = list(wallet[1]), _filesystem_entry(path)
    with pytest.raises(HistorySecurityError):
        Store(settings)
    assert wallet[1] == calls and _filesystem_entry(path) == before
    assert (outside / 'existing').read_bytes() == b'outside fixture must remain unchanged'
    assert not (outside / 'absent').exists()
