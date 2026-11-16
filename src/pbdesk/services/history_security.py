"""Explicit history protection, wallet enrollment and recovery.

Only opaque identity/version records live beside the encrypted database. Wallet
access is never part of inspection, and neither inspection nor Store enrolls keys.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
import uuid

from pbdesk.storage.cipher import (CipherUnavailable, connect, driver, present,
    sync_directory, validate_file)

BOOTSTRAP = '.pbdesk-history.json'
JOURNAL = '.pbdesk-history-migration.json'
MARKER = 'history_security_v1'


class HistorySecurityError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError()
    return value


def _read_record(path):
    validate_file(path, required=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4096:
            raise ValueError()
        value = json.load(stream)
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1:
        raise ValueError()
    _identity(value.get('uuid'))
    if path.name == BOOTSTRAP:
        _identity(value.get('key_uuid'))
        if value.get('phase') not in {'initializing', 'migrating', 'ready'} or set(value) != {'version', 'uuid', 'key_uuid', 'phase'}:
            raise ValueError()
    elif path.name == JOURNAL:
        if value.get('phase') not in {'exporting', 'verified', 'switched'} or set(value) != {'version', 'uuid', 'phase'}:
            raise ValueError()
    return value


def _write_record(path, value):
    validate_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.history-record-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        validate_file(path)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _plaintext(path):
    if not present(path):
        return False
    validate_file(path, required=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError()
        return stream.read(16) == b'SQLite format 3\x00'


def _marker(connection):
    row = connection.execute('SELECT value FROM settings WHERE key=?', (MARKER,)).fetchone()
    value = json.loads(row[0]) if row else None
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1:
        raise ValueError()
    _identity(value.get('uuid'))
    if type(value.get('recovery_offer_pending')) is not bool:
        raise ValueError()
    return value


def existing_key(settings, *, interactive=False):
    """Resolve only an established identity and verify it before any migrations."""
    from pbdesk.services.kwallet import read_database_key
    try:
        from pbdesk.storage.history_migration import validate_named_paths
        validate_named_paths(settings)
        record = _read_record(settings.application_dir / BOOTSTRAP)
        validate_named_paths(settings, identities=(record['uuid'],))
        if record['phase'] != 'ready' or present(settings.application_dir / JOURNAL):
            raise ValueError()
    except Exception:
        raise HistorySecurityError('recovery_required', 'History protection must be set up or recovered before opening history.') from None
    path = settings.application_dir / 'pbdesk.sqlite3'
    if not present(path) or _plaintext(path):
        raise HistorySecurityError('migration_required', 'Existing history requires explicit verified protection.')
    try:
        key = read_database_key(record.get('key_uuid', record['uuid']), interactive=interactive)
    except Exception:
        raise HistorySecurityError('locked', 'Unlock your existing KWallet and allow pbdesk access, or use your recovery key.') from None
    try:
        conn = connect(path, key, readonly=True)
        try:
            if _marker(conn)['uuid'] != record['uuid']:
                raise ValueError()
        finally:
            conn.close()
    except Exception:
        raise HistorySecurityError('recovery_required', 'The history key or protection record could not be verified. Use your recovery key.') from None
    return key


class HistorySecurity:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.application_dir / 'pbdesk.sqlite3'
        self._store = None
        self._key = None
        self._marker_cache = None

    @contextmanager
    def _mutation(self):
        root = self.settings.application_dir
        root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root / '.pbdesk-history.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise HistorySecurityError('unavailable', 'The history protection ownership lock is inaccessible.')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError:
            raise HistorySecurityError('busy', 'Another history protection operation is in progress.') from None
        finally:
            os.close(fd)

    def inspect(self):
        result = {'state': 'new', 'recovery_offer_pending': False, 'error': None, 'paths': []}
        try:
            driver()
        except CipherUnavailable:
            return {**result, 'state': 'unavailable', 'error': 'Encrypted history requires the SQLCipher driver.'}
        try:
            from pbdesk.storage.history_migration import (ARTIFACTS, inventory,
                migration_paths, validate_named_paths)
            validate_named_paths(self.settings)
            for name in (BOOTSTRAP, JOURNAL):
                control_path = self.settings.application_dir / name
                if present(control_path):
                    control = _read_record(control_path)
                    validate_named_paths(self.settings, identities=(control['uuid'],))
            selected_paths = inventory(self.settings)
            result['paths'] = [str(path) for path in selected_paths]
            legacy = self.settings.state_dir.resolve() / 'pbdesk.sqlite3'
            distinct_legacy = legacy.resolve() != self.path.resolve() and present(legacy)
            if present(self.settings.application_dir / JOURNAL):
                _read_record(self.settings.application_dir / JOURNAL)
                result['state'] = 'migration_incomplete'
            elif distinct_legacy and present(self.path):
                if _plaintext(self.path) and _plaintext(legacy):
                    result.update(state='plaintext', error='Migration will verify that the retained legacy database is an unchanged ancestor before proceeding.')
                else:
                    result.update(state='conflict', error='Two history databases exist. Preserve both and reconcile them before continuing.')
            elif present(self.settings.application_dir / BOOTSTRAP) and _read_record(self.settings.application_dir / BOOTSTRAP).get('phase') == 'initializing':
                result.update(state='new', error='History protection was interrupted. Retry protection to finish with the existing key.')
            elif _plaintext(self.path) or (distinct_legacy and _plaintext(legacy)):
                result['state'] = 'plaintext'
            elif present(self.path):
                record = _read_record(self.settings.application_dir / BOOTSTRAP)
                if record['phase'] != 'ready':
                    raise ValueError()
                if self._store is not None and getattr(self._store, '_database_lock', None) is not None:
                    marker = self._marker_cache
                    if not marker or marker.get('uuid') != record['uuid']:
                        raise ValueError()
                    result.update(state='ready', recovery_offer_pending=marker['recovery_offer_pending'])
                else:
                    result['state'] = 'locked'
            elif present(self.settings.application_dir / BOOTSTRAP) and not distinct_legacy:
                record = _read_record(self.settings.application_dir / BOOTSTRAP)
                selected_names = [path.name for path in selected_paths]
                migration_files_exist = any(present(path) for path in
                    migration_paths(self.settings.application_dir, record['uuid']))
                if (record['phase'] != 'migrating' or not selected_names
                        or migration_files_exist
                        or any(name not in ARTIFACTS for name in selected_names)):
                    raise ValueError()
                # A JSON-only migration may stop before its first journal write.
                # Its explicit retry reuses the enrolled key and validates files.
                result['state'] = 'plaintext'
            elif distinct_legacy:
                raise ValueError()
            elif result['paths']:
                # Old AI/configuration files are explicitly archived too.
                result['state'] = 'plaintext'
        except Exception:
            result.update(state='recovery_required', error='The history protection record needs recovery. Existing files were preserved.')
        return result

    def _remember(self, store, key):
        self._store, self._key = store, key
        self._marker_cache = store.get_setting(MARKER)
        return store

    def open_existing(self, *, interactive=False):
        if self._store is not None and getattr(self._store, '_database_lock', None) is not None:
            return self._store
        state = self.inspect()['state']
        if state not in {'locked', 'ready'}:
            raise HistorySecurityError(state, 'Protect or recover history before opening the application.')
        key = existing_key(self.settings, interactive=interactive)
        from pbdesk.storage.database import Store
        try:
            return self._remember(Store(self.settings, database_key=key), key)
        except Exception:
            raise HistorySecurityError('unavailable', 'Encrypted history could not be opened. Existing files were preserved.') from None

    @staticmethod
    def _enroll(wallet=None):
        from pbdesk.services.kwallet import authorize_database_wallet, write_database_key
        try:
            selected = authorize_database_wallet(wallet)
            # Authorization must complete before random key generation.
            key, identity = secrets.token_bytes(32), str(uuid.uuid4())
            write_database_key(identity, key, selected)
            return identity, key
        except Exception:
            raise HistorySecurityError('locked', 'Unlock your existing KWallet and allow pbdesk to create a dedicated history entry.') from None

    def protect(self, *, wallet=None):
        from pbdesk.storage.database import Store
        from pbdesk.storage.history_migration import ownership, selected_roots
        from pbdesk.services.kwallet import read_database_key
        with self._mutation(), ownership(self.settings) as descriptors:
            if self.inspect()['state'] != 'new':
                raise HistorySecurityError('conflict', 'Existing history must be migrated, unlocked, or recovered.')
            bootstrap = self.settings.application_dir / BOOTSTRAP
            if present(bootstrap):
                try:
                    record = _read_record(bootstrap)
                    if record.get('phase') != 'initializing':
                        raise ValueError()
                    identity = record['uuid']
                    key = read_database_key(record.get('key_uuid', identity), interactive=True)
                    if present(self.path) and self.path.stat().st_size:
                        conn = connect(self.path, key, readonly=True)
                        try:
                            table = conn.execute("SELECT name FROM sqlite_master WHERE name='settings'").fetchone()
                            row = conn.execute('SELECT value FROM settings WHERE key=?', (MARKER,)).fetchone() if table else None
                            if row and _marker(conn)['uuid'] != identity:
                                raise ValueError()
                        finally:
                            conn.close()
                except Exception:
                    raise HistorySecurityError('recovery_required', 'Interrupted history protection could not be verified. Existing files were preserved.') from None
            else:
                identity, key = self._enroll(wallet)
                record = {'version': 1, 'uuid': identity, 'key_uuid': identity, 'phase': 'initializing'}
                _write_record(bootstrap, record)
            # Retain the exact ownership lock across initialization.
            roots = selected_roots(self.settings)
            descriptor = os.dup(descriptors[roots.index(self.settings.application_dir)])
            store = Store(self.settings, database_key=key, _lock_fd=descriptor)
            try:
                marker = store.get_setting(MARKER)
                if marker is None:
                    store.set_setting(MARKER, {'version': 1, 'uuid': identity, 'recovery_offer_pending': True})
                elif marker.get('uuid') != identity:
                    raise ValueError()
                _write_record(bootstrap, {**record, 'phase': 'ready'})
                return self._remember(store, key)
            except BaseException:
                store.close()
                raise

    def migrate(self, *, wallet=None):
        from pbdesk.storage.history_migration import migrate
        with self._mutation():
            state = self.inspect()['state']
            if state not in {'plaintext', 'migration_incomplete'}:
                raise HistorySecurityError('conflict', 'No unambiguous plaintext history is available for migration.')
            return migrate(self, wallet=wallet)

    def recovery_key(self):
        """Explicit backup action only; callers must keep the returned secret local."""
        store = self.open_existing()
        identity = store.get_setting(MARKER)['uuid']
        payload = uuid.UUID(identity).bytes + self._key
        checksum = hashlib.sha256(b'pbdesk-history-recovery-v1\x00' + payload).digest()[:8]
        return 'pbdesk-history-v1:' + base64.urlsafe_b64encode(payload + checksum).decode().rstrip('=')

    @staticmethod
    def _parse_recovery(value):
        try:
            if not isinstance(value, str) or len(value) > 200 or not value.startswith('pbdesk-history-v1:'):
                raise ValueError()
            encoded = value.split(':', 1)[1]
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            if len(raw) != 56 or not secrets.compare_digest(raw[-8:], hashlib.sha256(b'pbdesk-history-recovery-v1\x00' + raw[:-8]).digest()[:8]):
                raise ValueError()
            return str(uuid.UUID(bytes=raw[:16])), raw[16:48]
        except Exception:
            raise HistorySecurityError('invalid_recovery', 'The recovery key is invalid or incomplete.') from None

    def recover(self, recovery_key, *, wallet=None):
        from pbdesk.services.kwallet import authorize_database_wallet, write_database_key
        from pbdesk.storage.database import Store
        from pbdesk.storage.history_migration import ownership, selected_roots
        with self._mutation(), ownership(self.settings) as descriptors:
            identity, key = self._parse_recovery(recovery_key)
            from pbdesk.storage.history_migration import validate_named_paths
            validate_named_paths(self.settings, identities=(identity,))
            # Validate the existing ciphertext before wallet or metadata mutation.
            try:
                conn = connect(self.path, key, readonly=True)
                try:
                    marker = _marker(conn)
                    if marker['uuid'] != identity or conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError()
                finally:
                    conn.close()
            except Exception:
                raise HistorySecurityError('invalid_recovery', 'This recovery key does not unlock the existing history.') from None
            if self._store is not None and getattr(self._store, '_database_lock', None) is not None:
                raise HistorySecurityError('busy', 'Close the open history before recovering its wallet entry.')
            try:
                selected = authorize_database_wallet(wallet)
                new_identity = str(uuid.uuid4())
                write_database_key(new_identity, key, selected)
            except Exception:
                raise HistorySecurityError('locked', 'Unlock an existing KWallet and allow a new dedicated history entry.') from None
            # The database identity stays stable: existing backups remain valid.
            roots = selected_roots(self.settings)
            descriptor = os.dup(descriptors[roots.index(self.settings.application_dir)])
            store = Store(self.settings, database_key=key, _lock_fd=descriptor)
            try:
                store.set_setting(MARKER, {'version': 1, 'uuid': identity, 'recovery_offer_pending': True})
                _write_record(self.settings.application_dir / BOOTSTRAP, {'version': 1, 'uuid': identity, 'key_uuid': new_identity, 'phase': 'ready'})
                return self._remember(store, key)
            except BaseException:
                store.close()
                raise

    def finish_recovery_offer(self, store, *, saved):
        if store is not self._store or type(saved) is not bool:
            raise HistorySecurityError('conflict', 'Unlock the current history before completing recovery setup.')
        marker = store.get_setting(MARKER)
        marker = {**marker, 'recovery_offer_pending': False, 'recovery_backup_saved': saved}
        store.set_setting(MARKER, marker)
        self._marker_cache = marker
