"""SQLite authority, retained legacy import and explicit first-run connection setup."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.api.configuration import snapshot, update_preferences
from pbdesk.config import Settings
from pbdesk.configuration import get, save_provider
from pbdesk.credentials import service_token, provider_credential_source
from pbdesk.storage.database import Store


def auth(settings, role='desktop'):
    return {'Authorization': 'Bearer ' + service_token(settings, role)}


class FakeProvider:
    state = 'connected'
    send_ready = True
    async def run(self):
        await asyncio.Event().wait()
    async def close(self):
        pass
    async def reload_encryption(self):
        pass


def application(settings):
    return create_app(settings, provider_factory=lambda *args: FakeProvider())


def test_legacy_import_retains_originals_and_sql_remains_authoritative(tmp_path):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    settings.state_dir.mkdir()
    values = {'provider-credential.json': {'backend': 'kwallet', 'wallet': 'existing',
        'folder': 'Pushbullet', 'entry': 'api-key', 'secret': 'must-not-import'},
        'ai-settings.json': {'model': 'gpt-6-luna', 'configured': True, 'revision': 'kept'},
        'ui-preferences.json': {'main_page': 'sms', 'color_scheme': 'dark', 'future': 42}}
    for name, value in values.items():
        path = settings.state_dir / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    originals = {name: (settings.state_dir / name).read_bytes() for name in values}
    store = Store(settings)
    try:
        assert not snapshot(store)['setup']['required']
        assert provider_credential_source(settings)['wallet'] == 'existing'
        assert get(settings, 'ai')['revision'] == 'kept'
        assert get(settings, 'ui') == values['ui-preferences.json']
        assert 'must-not-import' not in json.dumps(get(settings, 'provider'))
        assert all((settings.state_dir / name).read_bytes() == content for name, content in originals.items())
        update_preferences(store, {'main_page': 'summary'})
    finally:
        store.close()
    for name in values:
        (settings.state_dir / name).write_text('invalid legacy content must now be ignored')
    store = Store(settings)
    try:
        assert get(settings, 'ui')['main_page'] == 'summary'
        assert get(settings, 'ai')['configured'] is True
        assert provider_credential_source(settings)['wallet'] == 'existing'
        assert not snapshot(store)['setup']['required']
    finally:
        store.close()


def test_invalid_legacy_credentials_do_not_silently_become_new_setup(tmp_path):
    settings = Settings(tmp_path)
    path = tmp_path / 'provider-credential.json'
    path.write_text('{broken')
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match='originals were preserved'):
        Store(settings)
    assert path.read_text() == '{broken'
    path.write_text(json.dumps({'backend': 'keyring'}))
    store = Store(settings)
    assert snapshot(store)['setup']['completed']
    store.close()


def test_preference_patches_merge_atomically_in_existing_database(tmp_path):
    settings = Settings(tmp_path)
    store = Store(settings)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: update_preferences(store, {f'field_{index}': index}), range(24)))
        assert get(settings, 'ui') == {f'field_{index}': index for index in range(24)}
        assert not (tmp_path / 'ui-preferences.json').exists()
    finally:
        store.close()


def test_fresh_start_does_not_read_wallet_before_setup(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr('pbdesk.api.app.provider_token', lambda *args: pytest.fail('No implicit credential read'))
    with TestClient(create_app(settings)) as client:
        value = client.get('/api/v1/configuration', headers=auth(settings)).json()
        assert value['storage'] == 'sqlite' and value['setup']['required']
        assert not value['setup']['completed']


def test_configuration_is_desktop_only_and_demo_never_prompts(tmp_path):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, demo=True)) as client:
        for role in ('web', 'mcp'):
            assert client.get('/api/v1/configuration', headers=auth(settings, role)).status_code == 403
            assert client.patch('/api/v1/configuration/preferences', json={'color_scheme': 'dark'}, headers=auth(settings, role)).status_code == 403
        value = client.get('/api/v1/configuration', headers=auth(settings)).json()
        assert not value['setup']['required'] and not value['setup']['enabled']
        assert client.patch('/api/v1/configuration/preferences', json={'color_scheme': 'dark'}, headers=auth(settings)).json()['preferences']['color_scheme'] == 'dark'
        assert client.post('/api/v1/configuration/provider', json={'expected_revision': 0}, headers=auth(settings)).status_code == 409
        response = client.patch('/api/v1/configuration/preferences', json={'token': 'private-value'}, headers=auth(settings))
        assert response.status_code == 422 and 'private-value' not in response.text


def test_existing_entry_is_verified_saved_without_secret_and_survives_restart(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    calls = []
    async def verify(token):
        calls.append(token)
    monkeypatch.setattr('pbdesk.api.configuration.verify_token', verify)
    monkeypatch.setattr('pbdesk.services.kwallet.read_password', lambda *args, **kwargs: 'fixture-private-token')
    monkeypatch.setattr('pbdesk.services.kwallet.write_provider_password', lambda *args: pytest.fail('Existing entries are read only'))
    app = application(settings)
    with TestClient(app) as client:
        response = client.post('/api/v1/configuration/provider', json={'wallet': 'existing', 'expected_revision': 0}, headers=auth(settings))
        assert response.status_code == 200
        assert response.json()['setup']['completed'] and not response.json()['setup']['required']
        assert get(settings, 'provider')['wallet'] == 'existing'
        assert calls == ['fixture-private-token']
        assert 'fixture-private-token' not in response.text
        with app.state.store.engine.connect() as conn:
            assert 'fixture-private-token' not in repr(conn.exec_driver_sql('SELECT * FROM settings').fetchall())
        assert client.post('/api/v1/configuration/provider', json={'expected_revision': 0}, headers=auth(settings)).status_code == 409
        assert len(calls) == 1
    with TestClient(application(settings)) as client:
        value = client.get('/api/v1/configuration', headers=auth(settings)).json()
        assert value['setup']['source']['wallet'] == 'existing' and not value['setup']['required']


@pytest.mark.parametrize('outcome', ['invalid_token', 'wallet_denied', 'success'])
def test_new_token_only_completes_after_validation_and_verified_wallet_write(tmp_path, monkeypatch, outcome):
    from pbdesk.services.kwallet import KWalletUnavailable
    settings = Settings(tmp_path)
    writes = []
    async def verify(token):
        if outcome == 'invalid_token':
            raise HTTPException(422, 'Pushbullet rejected this access token.')
    def write(*args):
        writes.append(args)
        if outcome == 'wallet_denied':
            raise KWalletUnavailable('private error text')
    monkeypatch.setattr('pbdesk.api.configuration.verify_token', verify)
    monkeypatch.setattr('pbdesk.services.kwallet.write_provider_password', write)
    with TestClient(application(settings)) as client:
        response = client.post('/api/v1/configuration/provider', json={'token': 'fixture-secret', 'expected_revision': 0}, headers=auth(settings))
        assert response.status_code == {'invalid_token': 422, 'wallet_denied': 409, 'success': 200}[outcome]
        assert 'fixture-secret' not in response.text and 'private error text' not in response.text
        value = client.get('/api/v1/configuration', headers=auth(settings)).json()
        assert value['setup']['completed'] == (outcome == 'success')
        assert len(writes) == (0 if outcome == 'invalid_token' else 1)


def test_wallet_lock_does_not_reset_an_established_installation(tmp_path, monkeypatch):
    from pbdesk.credentials import CredentialLocked
    settings = Settings(tmp_path)
    save_provider(settings, {'backend': 'keyring'})
    monkeypatch.setattr('pbdesk.api.app.provider_token', lambda *args: (_ for _ in ()).throw(CredentialLocked('locked')))
    with TestClient(create_app(settings)) as client:
        assert not client.get('/api/v1/configuration', headers=auth(settings)).json()['setup']['required']
        assert client.get('/api/v1/capabilities', headers=auth(settings)).json()['provider_state'] == 'locked'
