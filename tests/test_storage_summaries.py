"""Synthetic, private-directory summary repository and additive migration checks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from pbdesk.config import Settings
from pbdesk.storage.database import Store
from pbdesk.storage.summaries import SummaryConflict, SummaryNotFound

NOW = '2026-11-14T12:00:00+00:00'


@pytest.fixture
def store(tmp_path):
    state, app = tmp_path / 'state', tmp_path / 'app'
    state.mkdir(mode=0o700)
    app.mkdir(mode=0o700)
    value = Store(Settings(state, application_dir=app))
    yield value
    value.close()


def config(name='Synthetic preset', **updates):
    return {**dict(name=name, sources=['notifications', 'sms'], device_ids=[], conversations=[],
                   conversation_filter='all', window_hours=24, model_override=None), **updates}


def data(repo, preset, fingerprint='synthetic-content', at=NOW, markdown='Synthetic 100%_complete summary'):
    return dict(canonical_config_hash=repo.canonical_config_hash(preset), resolved_model='fake-model',
                prompt_version='synthetic-v1', input_fingerprint=fingerprint, markdown=markdown,
                generated_at=at, notification_count=3, sms_count=4, conversation_count=2,
                window_start='2026-11-13T12:00:00+00:00', window_end=NOW,
                covered_oldest='2026-11-14T01:00:00+00:00', covered_newest=NOW,
                truncated=False, latency_ms=123, input_tokens=120, output_tokens=20,
                cached_input_tokens=None, actual_response_model='fake-actual')


def test_presets_revisions_active_archive_and_restart(store):
    repo = store.summaries
    defaults = repo.list_presets('a')
    assert {x['name'] for x in defaults} == {'All activity', 'Notifications only'}
    assert len(repo.list_presets('b')) == 2
    active = repo.get_active_preset('a')
    pid = str(uuid.uuid4())
    preset = repo.save_preset('a', config(), preset_id=pid)
    other = repo.save_preset('b', config('Other account'), preset_id=pid)
    assert preset['id'] == other['id']
    assert repo.set_active_preset('a', pid, active['revision'])['revision'] == 2
    with pytest.raises(SummaryConflict):
        repo.set_active_preset('a', pid, 1)
    changed = repo.save_preset('a', config('Edited'), preset_id=pid, expected_revision=1)
    assert changed['revision'] == 2
    with pytest.raises(SummaryConflict):
        repo.save_preset('a', config(), preset_id=pid, expected_revision=1)
    archived = repo.archive_preset('a', pid, 2)
    assert archived['revision'] == 3 and archived['archived']
    assert repo.get_active_preset('a')['preset']['id'] != pid
    assert repo.list_presets('b')[-1]['name'] == 'Other account'
    assert repo.archive_preset('a', pid, 3) == archived
    with pytest.raises(SummaryNotFound):
        repo.set_active_preset('a', other['id'], repo.get_active_preset('a')['revision'])
    assert len(repo.list_presets('a', include_archived=True)) == 3
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    settings = Settings(store.path.parent.parent / 'state', application_dir=store.path.parent)
    store.close()
    reopened = Store(settings)
    try:
        assert reopened.summaries.get_active_preset('a')['preset']['id'] != pid
        assert reopened.summaries.list_presets('a', True)[-1]['archived']
        assert reopened.summaries.version('a')['active_revision'] == 3
    finally:
        reopened.close()


def test_selected_conversations_and_normalization(store):
    repo = store.summaries
    for bad in (None, ''):
        with pytest.raises(ValueError):
            repo.list_presets(bad)
    with pytest.raises(ValueError):
        repo.save_preset('a', config(conversation_filter='selected'))
    # Exact (device, conversation) pairs remain distinct across devices.
    pairs = [dict(device_id='one', conversation_id='same'), dict(device_id='two', conversation_id='same')]
    selected = repo.save_preset('a', config(conversation_filter='selected', conversations=pairs))
    assert selected['conversations'] == pairs
    reverse = {**selected, 'conversations': list(reversed(pairs)), 'name': 'Rename', 'sources': ['sms', 'notifications']}
    assert repo.canonical_config_hash(selected) == repo.canonical_config_hash(reverse)
    for bad in (True, 0, 12):
        with pytest.raises(ValueError):
            repo.save_preset('a', {**config(), 'window_hours': bad})
    for bad in (['x'], 'sms', [], [None]):
        with pytest.raises(ValueError):
            repo.save_preset('a', {**config(), 'sources': bad})


def test_shared_immutable_generations_and_origin_snapshots(store):
    repo = store.summaries
    preset = repo.save_preset('a', config())
    first = repo.save_generation('a', data(repo, preset), preset)
    changed_data = {**data(repo, preset), 'markdown': 'Must not overwrite', 'input_tokens': 999}
    again = repo.save_generation('a', changed_data, {**preset, 'name': 'Never relabel'}, saved_at='2026-11-15T00:00:00Z')
    assert again == first
    clone = repo.save_preset('a', config('Clone'))
    second = repo.save_generation('a', changed_data, clone, saved_at='2026-11-15T00:00:00Z')
    assert second['generation_id'] == first['generation_id'] and second['id'] != first['id']
    assert second['preset_snapshot']['name'] == 'Clone'
    assert second['generated_at'] == NOW and second['saved_at'] == '2026-11-15T00:00:00+00:00'
    assert second['markdown'] == first['markdown'] and second['input_tokens'] == 120
    assert second['cached_input_tokens'] is None
    assert first['preset_snapshot'] == preset
    assert first['reuse_count'] == second['reuse_count'] == 0
    assert repo.record_reuse('a', second['id'])['reuse_count'] == 1
    assert repo.get_history('a', first['id'])['reuse_count'] == 0
    found = repo.lookup_generation('a', data(repo, preset)['canonical_config_hash'], 'fake-model', 'synthetic-v1', 'synthetic-content')
    assert found['id'] == first['generation_id']
    assert repo.lookup_generation('b', data(repo, preset)['canonical_config_hash'], 'fake-model', 'synthetic-v1', 'synthetic-content') is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(lambda _: repo.save_generation('a', data(repo, preset), preset), range(12)))
    assert {x['id'] for x in entries} == {first['id']}
    repo.archive_preset('a', preset['id'], 1)
    assert repo.list_history('a', preset_id=preset['id'])['total'] == 1
    assert repo.get_history('b', first['id']) is None
    with pytest.raises(SummaryNotFound):
        repo.set_pin('b', first['id'], True)


def test_literal_search_pagination_scopes_dates_and_metrics(store):
    repo = store.summaries
    preset = repo.save_preset('a', config())
    entry = repo.save_generation('a', data(repo, preset), preset)
    other = repo.save_generation('a', data(repo, preset, 'other', markdown='Synthetic 100Xcomplete'), preset)
    assert repo.list_history('a', query='%_')['total'] == 1
    assert repo.list_history('a', query='100%_complete')['items'][0]['id'] == entry['id']
    assert repo.list_history('a', query='100X')['items'][0]['id'] == other['id']
    assert repo.list_history('a', query="' OR 1=1 --")['total'] == 0
    assert repo.list_history('a', limit=1)['total'] == 2
    assert len(repo.list_history('a', limit=1, offset=1)['items']) == 1
    assert repo.list_history('b')['total'] == 0
    repo.set_pin('a', entry['id'], True)
    assert repo.list_history('a', pinned=True)['total'] == 1
    assert repo.list_history('a', date_from=NOW)['total'] == 2
    assert repo.list_history('a', date_to=NOW)['total'] == 0
    for changes in ({'sms_count': True}, {'input_tokens': -1}, {'output_tokens': 1.2}, {'markdown': 'x'*16001}, {'truncated': 1}, {'generated_at': '2026-01-01'}):
        with pytest.raises(ValueError):
            repo.save_generation('a', {**data(repo, preset), **changes}, preset)
    with pytest.raises(ValueError):
        repo.list_history('a', query='x'*257)
    with pytest.raises(ValueError):
        repo.list_history('a', limit=101)
    with pytest.raises(ValueError):
        repo.list_history('a', offset=-1)


def test_retention_preview_revision_boundary_pins_and_shared_cleanup(store):
    repo = store.summaries
    p = repo.save_preset('a', config())
    q = repo.save_preset('a', config('Clone'))
    cutoff = (datetime.fromisoformat(NOW)-timedelta(days=7)).isoformat()
    before = (datetime.fromisoformat(cutoff)-timedelta(microseconds=1)).isoformat()
    old = repo.save_generation('a', data(repo, p, 'old'), p, saved_at=before)
    pin = repo.save_generation('a', data(repo, p, 'pinned'), p, saved_at=before)
    repo.set_pin('a', pin['id'], True)
    boundary = repo.save_generation('a', data(repo, p, 'boundary'), p, saved_at=cutoff)
    clone = repo.save_generation('a', data(repo, p, 'old'), q, saved_at=NOW)
    b = repo.save_preset('b', config())
    foreign = repo.save_generation('b', data(repo, b, 'foreign'), b, saved_at=before)
    assert repo.get_retention('a') == dict(retention_days=30, revision=1)
    assert repo.preview_retention('a', 7, 1, now=NOW)['affected_count'] == 1
    assert repo.get_history('a', old['id'])
    with pytest.raises(ValueError):
        repo.set_retention('a', 7, 1, 30, now=NOW)
    applied = repo.set_retention('a', 7, 1, 7, now=NOW)
    assert applied == dict(retention_days=7, revision=2, removed_count=1)
    with pytest.raises(SummaryConflict):
        repo.set_retention('a', 7, 1, 7, now=NOW)
    assert repo.get_history('a', old['id']) is None
    assert repo.get_history('a', clone['id'])['generation_id'] == old['generation_id']
    assert repo.get_history('a', pin['id']) and repo.get_history('a', boundary['id'])
    assert repo.get_history('b', foreign['id'])
    assert repo.prune('a', now=NOW) == 0
    repo.set_pin('a', pin['id'], False)
    assert repo.prune('a', now=NOW) == 1
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM summary_generations WHERE id=?", (pin['generation_id'],)).scalar() == 0
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []


def test_legacy_import_null_context_idempotency_and_restart(store):
    repo = store.summaries
    legacy = dict(markdown='Synthetic historical digest', generated_at=NOW, resolved_model='old-fake-model')
    first = repo.import_legacy('a', legacy)
    assert first['origin'] == 'legacy' and first['preset_snapshot'] is None
    assert first['window_start'] is None and first['notification_count'] is None
    assert first['input_tokens'] is None and first['truncated'] is None
    assert repo.import_legacy('a', legacy) == first
    assert repo.import_legacy('b', legacy)['generation_id'] != first['generation_id']
    settings = Settings(store.path.parent.parent / 'state', application_dir=store.path.parent)
    store.close()
    reopened = Store(settings)
    try:
        assert reopened.summaries.import_legacy('a', legacy) == first
    finally:
        reopened.close()


def test_additive_0006_migration_preserves_every_existing_table(tmp_path):
    # Build synthetic preexisting 0006, never open the working application DB.
    root = tmp_path / 'app'
    root.mkdir(mode=0o700)
    path = root / 'pbdesk.sqlite3'
    engine = create_engine(f'sqlite:///{path}')
    migration = Config()
    migration.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'src/pbdesk/storage/migrations'))
    with engine.begin() as conn:
        migration.attributes['connection'] = conn
        command.upgrade(migration, '0006')
        # Populate each ordinary table, including nullable media/cipher/key fields.
        order = ['items', 'events', 'settings', 'devices', 'actions', 'action_audit', 'locked_envelopes', 'sms_messages', 'sms_sync', 'sms_threads', 'sms_envelopes', 'device_encryption']
        for table in order:
            values = {}
            for col in inspect(conn).get_columns(table):
                name = col['name']
                kind = str(col['type'])
                values[name] = 1 if ('INT' in kind or 'FLOAT' in kind) else ('"synthetic-value"' if table == 'settings' and name == 'value' else 'synthetic-'+name)
                if name in ('addresses', 'image_urls', 'recipients'):
                    values[name] = '["synthetic"]'
            names = ','.join('"'+k+'"' for k in values)
            conn.exec_driver_sql(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
        def capture():
            return {name: hashlib.sha256(repr(conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY rowid').all()).encode()).hexdigest() for name in order}
        original = capture()
        schema = conn.exec_driver_sql("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").all()
        migration.attributes['connection'] = conn
        command.upgrade(migration, '0007')
        assert capture() == original
        after = conn.exec_driver_sql("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'summary_%' AND name NOT LIKE 'ix_summary_%' ORDER BY type,name").all()
        assert after == schema
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    engine.dispose()
    store = Store(Settings(tmp_path / 'state', application_dir=root))
    try:
        assert store.path == path
        assert len(store.summaries.list_presets('synthetic-account')) == 2
        with store.engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == '0007'
            assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
            assert conn.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
            assert conn.exec_driver_sql('SELECT entry FROM device_encryption').scalar() == 'synthetic-entry'
            assert conn.exec_driver_sql('SELECT payload FROM sms_envelopes').scalar() == 'synthetic-payload'
    finally:
        store.close()
