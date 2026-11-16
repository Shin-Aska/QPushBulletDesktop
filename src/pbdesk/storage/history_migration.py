"""Explicit, WAL-aware plaintext to SQLCipher migration.

The durable journal contains only an opaque identity and a phase. The verified
rollback snapshot and the selected legacy files are retained inside SQLCipher.
No temporary plaintext export is produced.
"""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from pbdesk.storage.cipher import (SIDECARS, connect, create_private_file, driver,
    present, sync_file, sync_directory, validate_file)

ARTIFACTS = ('ai-digest-cache.json', 'ai-runtime-state.json', 'provider-credential.json',
             'ai-settings.json', 'ui-preferences.json')
ARCHIVE_TABLE = '_pbdesk_migration_archive_v1'
ANCESTOR_TABLE = '_pbdesk_migration_ancestor_v1'


def selected_roots(settings):
    return sorted({settings.application_dir.resolve(), settings.state_dir.resolve()}, key=str)


def inventory(settings):
    """Known selected application/state paths only; never recursively scan backups."""
    paths = []
    roots = selected_roots(settings)
    for root in roots:
        for name in ('pbdesk.sqlite3', *("pbdesk.sqlite3" + suffix for suffix in SIDECARS), *ARTIFACTS):
            path = root / name
            if present(path):
                paths.append(path)
    return paths


def migration_paths(root, identity):
    return [root / ('.pbdesk-history-' + identity + ending + suffix)
        for ending in ('.stage', '.archive', '.ancestor.archive')
        for suffix in ('', *SIDECARS)]


def validate_named_paths(settings, *, identities=()):
    """Validate only the selected source/control and opaque migration entries."""
    from pbdesk.services.history_security import BOOTSTRAP, JOURNAL
    for path in inventory(settings):
        validate_file(path, required=True)
    for root in selected_roots(settings):
        database = root / 'pbdesk.sqlite3'
        if not present(database) and any(present(root / ('pbdesk.sqlite3' + suffix)) for suffix in SIDECARS):
            raise ValueError('Orphan database sidecars require review before migration')
    for name in (BOOTSTRAP, JOURNAL):
        validate_file(settings.application_dir / name)
    for identity in identities:
        for path in migration_paths(settings.application_dir, identity):
            validate_file(path)


def _read_private(path):
    validate_file(path, required=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
            raise ValueError()
        # Legacy caches are bounded by the old implementation; refuse surprises.
        if info.st_size > 16 * 1024 * 1024:
            raise ValueError()
        return stream.read()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def logical_digest(connection, *, exclude_tables=(), skip_security=False):
    """Deterministic schema/rows including FTS, sequence and user_version."""
    digest = hashlib.sha256()
    schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    schema = [row for row in schema if row[1] not in exclude_tables and row[2] not in exclude_tables]
    def encode(value):
        if isinstance(value, bytes):
            return {'bytes': base64.b64encode(value).decode()}
        return value
    def feed(value):
        digest.update(json.dumps(value, ensure_ascii=True, separators=(',', ':'), default=encode).encode())
        digest.update(b'\n')
    feed(schema)
    for kind, name, table, sql in schema:
        if kind != 'table':
            continue
        feed(name)
        # Sorting serialized rows also covers WITHOUT ROWID and composite keys.
        rows = [json.dumps(row, ensure_ascii=True, separators=(',', ':'), default=encode)
                for row in connection.execute('SELECT * FROM ' + _quote(name))
                if not (skip_security and name == 'settings' and row[0] == 'history_security_v1')]
        for row in sorted(rows):
            digest.update(row.encode())
            digest.update(b'\n')
    feed(connection.execute('PRAGMA user_version').fetchone()[0])
    return digest.hexdigest()


def _integrity(connection):
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError()
    cipher = connection.execute('PRAGMA cipher_integrity_check').fetchall()
    if cipher:
        raise ValueError()


@contextmanager
def ownership(settings):
    descriptors = []
    try:
        # The legacy core may have used state_dir as its application directory.
        for root in selected_roots(settings):
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink() or root.stat().st_uid != os.getuid():
                raise ValueError()
            fd = os.open(root / '.pbdesk-database.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise ValueError()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                from pbdesk.services.history_security import HistorySecurityError
                raise HistorySecurityError('busy', 'A pbdesk core already owns this history. Close it before changing history protection.') from None
        yield descriptors
    finally:
        for fd in descriptors:
            os.close(fd)


def _journal(settings, identity, phase):
    from pbdesk.services.history_security import JOURNAL, _write_record
    _write_record(settings.application_dir / JOURNAL, {'version': 1, 'uuid': identity, 'phase': phase})


def _checkpoint(connection):
    connection.commit()
    row = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if row and row[0] != 0:
        raise ValueError()
    connection.execute('PRAGMA journal_mode=DELETE')


def _staging(root, identity):
    return root / ('.pbdesk-history-' + identity + '.stage')


def _archive(root, identity):
    return root / ('.pbdesk-history-' + identity + '.archive')


def _select_source(settings):
    from pbdesk.services.history_security import _plaintext
    target = settings.application_dir / 'pbdesk.sqlite3'
    validate_named_paths(settings)
    candidates = [path for path in dict.fromkeys((target, settings.state_dir.resolve() / 'pbdesk.sqlite3')) if present(path)]
    if any(not _plaintext(path) for path in candidates):
        raise ValueError()
    if len(candidates) < 2:
        return (candidates[0] if candidates else None), None, None
    # The preceding release intentionally retained a verified legacy ancestor.
    # Read it only during the explicit migration action, never during inspection.
    import sqlite3
    ancestor = settings.state_dir.resolve() / 'pbdesk.sqlite3'
    with sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True) as primary:
        row = primary.execute("SELECT value FROM settings WHERE key='database_legacy_source'").fetchone()
        marker = json.loads(row[0]) if row else None
    connection = sqlite3.connect(ancestor.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('BEGIN')
        digest = hashlib.sha256()
        for line in connection.iterdump():
            digest.update(line.encode())
        if not marker or marker.get('path') != str(ancestor.resolve()) or marker.get('digest') != digest.hexdigest():
            raise ValueError()
        logical = logical_digest(connection)
    finally:
        connection.close()
    return target, ancestor, logical


def _export(source, stage, key):
    dbapi = driver()
    validate_file(source, required=True)
    for suffix in SIDECARS:
        validate_file(Path(str(source) + suffix))
    create_private_file(stage)
    connection = dbapi.connect(source.resolve().as_uri() + '?mode=rw', uri=True)
    try:
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('BEGIN IMMEDIATE')
        before = logical_digest(connection)
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        connection.execute('ATTACH DATABASE ? AS encrypted KEY "x\'' + key.hex() + '\'"', (str(stage),))
        connection.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        connection.execute('PRAGMA encrypted.user_version=' + str(int(version)))
        connection.commit()
        connection.execute('DETACH DATABASE encrypted')
        _checkpoint(connection)
        connection.execute('BEGIN EXCLUSIVE')
        if logical_digest(connection) != before:
            raise ValueError()
        target = connect(stage, key)
        try:
            _integrity(target)
            if logical_digest(target) != before:
                raise ValueError()
            _checkpoint(target)
        finally:
            target.close()
        return before, connection
    except BaseException:
        connection.close()
        raise


def _copy_encrypted(stage, archive):
    """Copy verified ciphertext through no-follow FDs into a new private entry."""
    validate_file(stage, required=True)
    source_fd = os.open(stage, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError()
        destination_fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(destination_fd, 'wb') as destination, os.fdopen(os.dup(source_fd), 'rb') as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
    finally:
        os.close(source_fd)


def _archive_snapshot(stage, archive, key, source, digest, settings, ancestor=None, ancestor_digest=None):
    # stage has been checkpointed and verified, so its byte copy stays encrypted.
    _copy_encrypted(stage, archive)
    conn = connect(archive, key)
    try:
        if logical_digest(conn) != digest:
            raise ValueError()
        conn.execute('CREATE TABLE ' + ARCHIVE_TABLE + '(path TEXT PRIMARY KEY, digest TEXT NOT NULL, content BLOB)')
        conn.execute('INSERT INTO ' + ARCHIVE_TABLE + ' VALUES (?,?,NULL)', (str(source) if source else '', digest))
        if ancestor is not None:
            conn.execute('CREATE TABLE ' + ANCESTOR_TABLE + '(path TEXT PRIMARY KEY, digest TEXT NOT NULL)')
            conn.execute('INSERT INTO ' + ANCESTOR_TABLE + ' VALUES (?,?)', (str(ancestor), ancestor_digest))
        for path in inventory(settings):
            if path.name not in ARTIFACTS:
                continue
            content = _read_private(path)
            conn.execute('INSERT INTO ' + ARCHIVE_TABLE + ' VALUES (?,?,?)',
                (str(path), hashlib.sha256(content).hexdigest(), content))
        conn.commit()
        _integrity(conn)
        _checkpoint(conn)
    finally:
        conn.close()
    sync_file(archive)
    sync_directory(archive.parent)


def _archive_manifest(archive, key):
    conn = connect(archive, key, readonly=True)
    try:
        _integrity(conn)
        rows = conn.execute('SELECT path,digest,content FROM ' + ARCHIVE_TABLE + ' ORDER BY path').fetchall()
        source_rows = [row for row in rows if row[2] is None]
        if len(source_rows) != 1:
            raise ValueError()
        if logical_digest(conn, exclude_tables=(ARCHIVE_TABLE, ANCESTOR_TABLE)) != source_rows[0][1]:
            raise ValueError()
        for path, digest, content in rows:
            if content is not None and hashlib.sha256(content).hexdigest() != digest:
                raise ValueError()
        table = conn.execute('SELECT name FROM sqlite_master WHERE name=?', (ANCESTOR_TABLE,)).fetchone()
        ancestor = conn.execute('SELECT path,digest FROM ' + ANCESTOR_TABLE).fetchone() if table else None
        return source_rows[0], [row for row in rows if row[2] is not None], ancestor
    finally:
        conn.close()


def _set_marker(path, key, identity):
    from pbdesk.services.history_security import MARKER
    conn = connect(path, key)
    try:
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', (MARKER,
            json.dumps({'version': 1, 'uuid': identity, 'recovery_offer_pending': True})))
        conn.commit()
        _integrity(conn)
        _checkpoint(conn)
    finally:
        conn.close()
    sync_file(path)


def _cleanup_artifacts(artifacts):
    for name, digest, content in artifacts:
        path = Path(name)
        if not present(path):
            continue
        if hashlib.sha256(_read_private(path)).hexdigest() != digest:
            raise ValueError()
        path.unlink()
        sync_directory(path.parent)


def _runtime_payloads(artifacts):
    values = {}
    for filename, content in artifacts:
        if filename not in {'ai-digest-cache.json', 'ai-runtime-state.json'}:
            continue
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        if filename in values and values[filename] != value:
            raise ValueError('Conflicting selected AI caches must be reconciled before migration')
        values[filename] = value
    return values


def migrate(security, *, wallet=None):
    from pbdesk.services.history_security import (BOOTSTRAP, JOURNAL, MARKER, HistorySecurityError,
        _read_record, _write_record, _plaintext, _marker)
    from pbdesk.services.kwallet import read_database_key
    from pbdesk.storage.database import Store
    from pbdesk.configuration import archived_configuration, import_archived
    settings, target = security.settings, security.path
    journal_path = settings.application_dir / JOURNAL
    source_connection = None
    ancestor_connection = None
    try:
        with ownership(settings):
            validate_named_paths(settings)
            if present(journal_path):
                journal = _read_record(journal_path)
                identity = journal['uuid']
                if _read_record(settings.application_dir / BOOTSTRAP)['uuid'] != identity:
                    raise ValueError()
                record = _read_record(settings.application_dir / BOOTSTRAP)
                validate_named_paths(settings, identities=(identity,))
                key = read_database_key(record.get('key_uuid', identity), interactive=True)
            else:
                _select_source(settings)
                selected = [(path.name, _read_private(path)) for path in inventory(settings) if path.name in ARTIFACTS]
                archived_configuration(selected)
                _runtime_payloads(selected)
                bootstrap = settings.application_dir / BOOTSTRAP
                if present(bootstrap):
                    record = _read_record(bootstrap)
                    if record.get('phase') != 'migrating':
                        raise ValueError()
                    identity = record['uuid']
                    validate_named_paths(settings, identities=(identity,))
                    key = read_database_key(record.get('key_uuid', identity), interactive=True)
                else:
                    identity, key = security._enroll(wallet)
                    _write_record(bootstrap, {'version': 1, 'uuid': identity, 'key_uuid': identity, 'phase': 'migrating'})
                _journal(settings, identity, 'exporting')
                journal = {'phase': 'exporting'}
            stage, archive = _staging(settings.application_dir, identity), _archive(settings.application_dir, identity)
            ancestor_archive = settings.application_dir / ('.pbdesk-history-' + identity + '.ancestor.archive')
            validate_named_paths(settings, identities=(identity,))
            phase = journal['phase']
            if phase not in {'exporting', 'verified', 'switched'}:
                raise ValueError()
            if phase == 'exporting':
                source, ancestor, expected_ancestor_digest = _select_source(settings)
                # Only unverified ciphertext artifacts belonging to this journal
                # may be replaced. Source databases are never deleted here.
                for candidate in migration_paths(settings.application_dir, identity):
                    if validate_file(candidate):
                        candidate.unlink()
                if source:
                    digest, source_connection = _export(source, stage, key)
                else:
                    from sqlalchemy import create_engine
                    from sqlalchemy.pool import NullPool
                    from alembic import command
                    from alembic.config import Config
                    engine = create_engine('sqlite://', module=driver(), poolclass=NullPool,
                        creator=lambda: connect(stage, key))
                    config = Config()
                    config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
                    try:
                        with engine.begin() as connection:
                            config.attributes['connection'] = connection
                            command.upgrade(config, 'head')
                    finally:
                        engine.dispose()
                    conn = connect(stage, key)
                    try:
                        digest = logical_digest(conn)
                        _checkpoint(conn)
                    finally:
                        conn.close()
                ancestor_digest = None
                if ancestor:
                    ancestor_digest, ancestor_connection = _export(ancestor, ancestor_archive, key)
                    if ancestor_digest != expected_ancestor_digest:
                        raise ValueError()
                    sync_file(ancestor_archive)
                _archive_snapshot(stage, archive, key, source, digest, settings, ancestor, ancestor_digest)
                _set_marker(stage, key, identity)
                sync_directory(settings.application_dir)
                _journal(settings, identity, 'verified')
                phase = 'verified'
            (source_name, original_digest, _), artifacts, ancestor_record = _archive_manifest(archive, key)
            imported_configuration = archived_configuration([(Path(name).name, content) for name, digest, content in artifacts])
            imported_runtime = _runtime_payloads([(Path(name).name, content) for name, digest, content in artifacts])
            if ancestor_record:
                ancestor_path, ancestor_digest = ancestor_record
                ancestor = Path(ancestor_path)
                if ancestor != settings.state_dir.resolve() / 'pbdesk.sqlite3' or ancestor == target:
                    raise ValueError()
                conn = connect(ancestor_archive, key, readonly=True)
                try:
                    _integrity(conn)
                    if logical_digest(conn) != ancestor_digest:
                        raise ValueError()
                finally:
                    conn.close()
                if present(ancestor) and ancestor_connection is None:
                    validate_file(ancestor, required=True)
                    ancestor_connection = driver().connect(ancestor.resolve().as_uri() + '?mode=rw', uri=True)
                    _checkpoint(ancestor_connection)
                    ancestor_connection.execute('BEGIN EXCLUSIVE')
                    if logical_digest(ancestor_connection) != ancestor_digest:
                        raise ValueError()
            source = Path(source_name) if source_name else None
            allowed = {str(path) for path in dict.fromkeys((target, settings.state_dir.resolve() / 'pbdesk.sqlite3'))}
            if source_name and source_name not in allowed:
                raise ValueError()
            if phase == 'verified':
                if present(target) and not _plaintext(target):
                    # The atomic rename completed before its journal update.
                    conn = connect(target, key, readonly=True)
                    try:
                        if _marker(conn)['uuid'] != identity:
                            raise ValueError()
                        _integrity(conn)
                    finally:
                        conn.close()
                else:
                    conn = connect(stage, key, readonly=True)
                    try:
                        if _marker(conn)['uuid'] != identity:
                            raise ValueError()
                        _integrity(conn)
                    finally:
                        conn.close()
                    if source and source_connection is None:
                        validate_file(source, required=True)
                        source_connection = driver().connect(source.resolve().as_uri() + '?mode=rw', uri=True)
                        _checkpoint(source_connection)
                        source_connection.execute('BEGIN EXCLUSIVE')
                        if logical_digest(source_connection) != original_digest:
                            raise ValueError()
                    if logical_digest(conn := connect(stage, key, readonly=True), skip_security=True) != original_digest:
                        conn.close()
                        raise ValueError()
                    conn.close()
                    validate_named_paths(settings, identities=(identity,))
                    os.replace(stage, target)
                    sync_directory(target.parent)
                _journal(settings, identity, 'switched')
            conn = connect(target, key, readonly=True)
            try:
                if _marker(conn)['uuid'] != identity:
                    raise ValueError()
                _integrity(conn)
            finally:
                conn.close()
            if source and source != target and present(source):
                if source_connection is None:
                    validate_file(source, required=True)
                    source_connection = driver().connect(source.resolve().as_uri() + '?mode=rw', uri=True)
                    _checkpoint(source_connection)
                    source_connection.execute('BEGIN EXCLUSIVE')
                if logical_digest(source_connection) != original_digest:
                    raise ValueError()
                for suffix in ('-wal', '-shm', '-journal', ''):
                    path = Path(str(source) + suffix)
                    if validate_file(path):
                        path.unlink()
                sync_directory(source.parent)
            if ancestor_record and present(ancestor):
                for suffix in ('-wal', '-shm', '-journal', ''):
                    path = Path(str(ancestor) + suffix)
                    if validate_file(path):
                        path.unlink()
                sync_directory(ancestor.parent)
            if ancestor_connection is not None:
                ancestor_connection.close()
                ancestor_connection = None
            if source_connection is not None:
                source_connection.close()
                source_connection = None
        # Store acquires ownership anew. Keep journal until configuration import,
        # migrations and selected plaintext cleanup have all succeeded.
        store = Store(settings, database_key=key)
        try:
            import_archived(store, imported_configuration)
            for name, value in imported_runtime.items():
                if store.get_setting('ai_runtime:' + name) is None:
                    store.set_setting('ai_runtime:' + name, value)
            _cleanup_artifacts(artifacts)
            record = _read_record(settings.application_dir / BOOTSTRAP)
            _write_record(settings.application_dir / BOOTSTRAP, {**record, 'phase': 'ready'})
            journal_path.unlink()
            sync_directory(journal_path.parent)
            return security._remember(store, key)
        except BaseException:
            store.close()
            raise
    except HistorySecurityError:
        raise
    except Exception:
        raise HistorySecurityError('migration_incomplete', 'History migration could not be completed; originals were preserved where still present, together with verified encrypted rollback material. Resolve the conflict and retry.') from None
    finally:
        if source_connection is not None:
            source_connection.close()
        if ancestor_connection is not None:
            ancestor_connection.close()
