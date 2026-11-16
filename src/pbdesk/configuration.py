"""Non-secret configuration in the existing core-owned application database."""
from contextlib import closing
import json
import os
import re
import sqlite3
import stat
import weakref

PREFIX = 'configuration:'
_stores = weakref.WeakValueDictionary()


def _key(settings):
    return (str(settings.application_dir), str(settings.state_dir.resolve()))


def register(settings, store):
    _stores[_key(settings)] = store


def unregister(settings, store):
    if _stores.get(_key(settings)) is store:
        _stores.pop(_key(settings), None)


def get(settings, name, default=None):
    store = _stores.get(_key(settings))
    if store is not None:
        return store.get_setting(PREFIX + name, default)
    # Offline local administration only; desktop clients use the private API.
    path = settings.application_dir / 'pbdesk.sqlite3'
    if not path.exists():
        return default
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise RuntimeError('Application configuration is inaccessible')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (PREFIX + name,)).fetchone()
    return json.loads(row[0]) if row else default


def put(settings, name, value):
    store = _stores.get(_key(settings))
    if store is not None:
        store.set_setting(PREFIX + name, value)
        return
    # An offline admin acquires the same ownership lock and migrations as core.
    from pbdesk.storage.database import Store
    store = Store(settings)
    try:
        store.set_setting(PREFIX + name, value)
    finally:
        store.close()


def provider_reference(value):
    if not isinstance(value, dict) or value.get('backend') not in {'keyring', 'kwallet'}:
        raise ValueError('Invalid credential reference')
    if value['backend'] == 'keyring':
        return {'backend': 'keyring'}
    for field in ('folder', 'entry'):
        if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > 1024:
            raise ValueError('Invalid credential reference')
    wallet = value.get('wallet')
    if wallet is not None and (not isinstance(wallet, str) or not wallet.strip() or len(wallet) > 1024):
        raise ValueError('Invalid wallet reference')
    return {'backend': 'kwallet', 'wallet': wallet, 'folder': value['folder'], 'entry': value['entry']}


def save_provider(settings, reference, expected_revision=None):
    """Commit the selected reference and completed setup in one transaction."""
    from sqlalchemy import select
    from sqlalchemy.dialects.sqlite import insert
    from pbdesk.storage.database import Store, settings_table
    reference = provider_reference(reference)
    store = _stores.get(_key(settings))
    owned = store is None
    if owned:
        store = Store(settings)
    try:
        with store.writing() as conn:
            raw = conn.execute(select(settings_table.c.value).where(
                settings_table.c.key == PREFIX + 'provider_revision')).scalar_one_or_none()
            revision = json.loads(raw) if raw is not None else 0
            if expected_revision is not None and expected_revision != revision:
                raise ValueError('Connection settings changed. Reload and try again.')
            for name, value in {'provider': reference, 'setup': {'completed': True, 'version': 1},
                    'provider_revision': revision + 1}.items():
                statement = insert(settings_table).values(key=PREFIX + name, value=json.dumps(value))
                conn.execute(statement.on_conflict_do_update(index_elements=['key'], set_={'value': statement.excluded.value}))
    finally:
        if owned:
            store.close()


def ai_settings(value):
    if (not isinstance(value, dict) or not isinstance(value.get('model'), str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,99}', value['model'])
            or type(value.get('configured')) is not bool):
        raise ValueError('Invalid AI settings')
    return {'model': value['model'], 'configured': value['configured'],
        'revision': str(value.get('revision', 'initial'))[:100]}


def import_legacy(settings, store):
    """Import once, atomically; existing SQL values win. Retain original files."""
    from sqlalchemy import select
    from sqlalchemy.dialects.sqlite import insert
    from pbdesk.storage.database import settings_table
    with store.engine.begin() as conn:
        if conn.execute(select(settings_table.c.value).where(
                settings_table.c.key == PREFIX + 'import_v1')).scalar_one_or_none() is not None:
            return
        imported = {}
        for filename, name, private, validate in (
                ('provider-credential.json', 'provider', True, provider_reference),
                ('ai-settings.json', 'ai', True, ai_settings),
                ('ui-preferences.json', 'ui', False, lambda value: value if isinstance(value, dict) else {})):
            if conn.execute(select(settings_table.c.value).where(
                    settings_table.c.key == PREFIX + name)).scalar_one_or_none() is not None:
                continue
            try:
                fd = os.open(settings.state_dir / filename, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                with os.fdopen(fd) as stream:
                    info = os.fstat(stream.fileno())
                    if info.st_uid != os.getuid() or (private and stat.S_IMODE(info.st_mode) != 0o600):
                        raise ValueError()
                    raw = stream.read(65537)
                    if len(raw) > 65536:
                        raise ValueError()
                    imported[name] = validate(json.loads(raw))
            except (OSError, ValueError, TypeError, KeyError):
                if name != 'ui':
                    raise RuntimeError('Legacy credential or AI configuration is invalid; originals were preserved') from None
        for name, value in imported.items():
            conn.execute(insert(settings_table).values(key=PREFIX + name, value=json.dumps(value))
                .on_conflict_do_nothing(index_elements=['key']))
        established = conn.execute(select(settings_table.c.value).where(
            settings_table.c.key.in_([PREFIX + 'provider', 'pushbullet_account_id']))).scalars().all()
        if any(json.loads(value) for value in established):
            conn.execute(insert(settings_table).values(key=PREFIX + 'setup',
                value=json.dumps({'completed': True, 'version': 1}))
                .on_conflict_do_nothing(index_elements=['key']))
        conn.execute(insert(settings_table).values(key=PREFIX + 'import_v1', value='true'))
