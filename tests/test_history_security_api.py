"""Bootstrap API uses isolated temporary SQLCipher databases and a fake wallet."""
import time
import pytest
from fastapi.testclient import TestClient
from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token, health_headers, verify_health
from pbdesk.services.history_security import HistorySecurity

pytestmark = pytest.mark.history_bootstrap


@pytest.fixture
def wallet(monkeypatch):
    keys = {}
    calls = []
    monkeypatch.setattr('pbdesk.services.kwallet.authorize_database_wallet', lambda wallet=None: (calls.append('authorize') or 'fictional-wallet'))
    monkeypatch.setattr('pbdesk.services.kwallet.write_database_key', lambda identity, key, wallet: keys.__setitem__(identity, key))
    def read(identity, *, interactive=False):
        calls.append('read')
        return keys[identity]
    monkeypatch.setattr('pbdesk.services.kwallet.read_database_key', read)
    return keys, calls


def headers(settings, role='desktop'):
    return {'Authorization': 'Bearer ' + service_token(settings, role)}


def wait_ready(client, settings):
    for _ in range(200):
        status = client.get('/api/v1/history/security', headers=headers(settings)).json()
        if status['history_ready']:
            return status
        time.sleep(.01)
    raise AssertionError('Encrypted history did not become ready')


def test_first_run_restricted_until_explicit_recovery_choice(tmp_path, wallet):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    provider_calls = []
    def factory(*args):
        provider_calls.append('start')
        return None
    app = create_app(settings, demo=True, provider_factory=factory)
    with TestClient(app) as client:
        proof = health_headers(settings, 'desktop')
        health = client.get('/api/v1/health', headers=proof).json()
        assert verify_health(settings, 'desktop', proof, health)
        assert health['ready'] and not health['history_ready']
        assert health['history_encryption'] == 1
        assert app.state.ai is None
        assert not provider_calls and not wallet[1]
        desktop = headers(settings)
        for method, path in [('GET', '/api/v1/items'), ('GET', '/api/v1/configuration'), ('GET', '/api/v1/core/settings'), ('POST', '/api/v1/ai/digest'), ('POST', '/api/v1/web')]:
            assert client.request(method, path, headers=desktop, json={}).status_code == 423
        assert client.get('/api/v1/items', headers=headers(settings, 'mcp')).status_code == 423
        assert client.post('/api/v1/history/protect', headers=headers(settings, 'web')).status_code == 403
        assert client.post('/api/v1/history/protect', headers={**desktop, 'Origin': 'http://foreign.invalid'}).status_code == 403
        assert client.get('/api/v1/history/security').status_code == 401
        cap = client.get('/api/v1/capabilities', headers=desktop).json()
        assert cap == {'features': {'history_encryption': True, 'client_lifecycle': True}, 'history_ready': False}
        protected = client.post('/api/v1/history/protect', headers=desktop)
        assert protected.status_code == 200, protected.text
        assert protected.json()['recovery_offer_pending']
        identity = next(iter(wallet[0]))
        # A lost successful response reconciles without minting a second key.
        assert client.post('/api/v1/history/protect', headers=desktop).status_code == 200
        assert list(wallet[0]) == [identity]
        assert not provider_calls and app.state.ai is None
        assert client.get('/api/v1/items', headers=desktop).status_code == 423
        key = client.post('/api/v1/history/recovery-key', headers=desktop).json()['recovery_key']
        assert key.startswith('pbdesk-history-v1:')
        done = client.post('/api/v1/history/recovery-offer', headers=desktop, json={'saved': False})
        assert done.status_code == 200 and done.json()['history_ready']
        assert provider_calls == ['start']
        assert client.get('/api/v1/items', headers=desktop).status_code == 200
        after = client.get('/api/v1/health', headers=health_headers(settings, 'desktop')).json()
        assert after['instance_id'] == health['instance_id'] == app.state.store.instance_id
        assert after['history_ready']


def test_recovery_validation_is_secret_safe_and_does_not_enroll(tmp_path, wallet):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    with TestClient(create_app(settings, demo=True)) as client:
        desktop = headers(settings)
        secret = 'fictional-private-recovery-material'
        for body in ({'recovery_key': secret}, {'recovery_key': {'nested': secret}}, {'recovery_key': secret, secret: secret}):
            response = client.post('/api/v1/history/recover', headers=desktop, json=body)
            assert response.status_code in {409, 422}
            assert secret not in response.text
        assert not wallet[0] and not wallet[1]


def test_restart_auto_opens_and_locked_wallet_stays_restricted(tmp_path, wallet, monkeypatch):
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    security = HistorySecurity(settings)
    store = security.protect()
    security.finish_recovery_offer(store, saved=False)
    store.close()
    with TestClient(create_app(settings, demo=True)) as client:
        wait_ready(client, settings)
        assert client.get('/api/v1/items', headers=headers(settings)).status_code == 200
    def locked(*args, **kwargs):
        raise RuntimeError('fictional locked wallet')
    monkeypatch.setattr('pbdesk.services.kwallet.read_database_key', locked)
    with TestClient(create_app(settings, demo=True)) as client:
        result = client.get('/api/v1/history/security', headers=headers(settings)).json()
        assert not result['history_ready']
        assert client.get('/api/v1/items', headers=headers(settings)).status_code == 423
        assert client.post('/api/v1/history/unlock', headers=headers(settings)).status_code == 409


def test_busy_wallet_keeps_health_status_and_client_leases_responsive(tmp_path, wallet, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from pbdesk.services.client_lifecycle import stop_generation
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    entered, release = threading.Event(), threading.Event()
    def authorize(wallet=None):
        entered.set()
        assert release.wait(5)
        return 'fictional-wallet'
    monkeypatch.setattr('pbdesk.services.kwallet.authorize_database_wallet', authorize)
    with TestClient(create_app(settings, demo=True)) as client:
        desktop = headers(settings)
        proof = health_headers(settings, 'desktop')
        identity = client.get('/api/v1/health', headers=proof).json()['instance_id']
        lease = client.post('/api/v1/clients', headers=desktop,
            json={'instance_id': identity, 'generation': stop_generation(settings)}).json()
        with ThreadPoolExecutor() as pool:
            operation = pool.submit(client.post, '/api/v1/history/protect', headers=desktop)
            assert entered.wait(3)
            try:
                started = time.monotonic()
                assert client.get('/api/v1/health', headers=health_headers(settings, 'desktop')).status_code == 200
                status = client.get('/api/v1/history/security', headers=desktop).json()
                assert status['busy'] and not status['history_ready']
                assert client.put('/api/v1/clients/' + lease['lease_id'], headers=desktop,
                    json={'instance_id': identity}).status_code == 200
                assert time.monotonic() - started < 1
            finally:
                release.set()
            assert operation.result().status_code == 200
        assert client.delete('/api/v1/clients/' + lease['lease_id'], headers=desktop,
            params={'instance_id': identity}).status_code == 200


def test_signed_health_binds_encryption_readiness(tmp_path):
    from pbdesk.credentials import health_response
    from uuid import uuid4
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    proof = health_headers(settings, 'desktop')
    body = health_response(settings, proof, str(uuid4()), True, history_ready=False)
    assert verify_health(settings, 'desktop', proof, body)
    body['history_ready'] = True
    assert not verify_health(settings, 'desktop', proof, body)


def test_cancelled_protection_holds_mutation_ownership_until_wallet_settles(tmp_path, wallet, monkeypatch):
    import asyncio
    import threading
    import httpx
    settings = Settings(tmp_path / 'state', application_dir=tmp_path / 'app')
    entered, release = threading.Event(), threading.Event()
    authorizations = []
    def authorize(wallet=None):
        authorizations.append(True)
        entered.set()
        assert release.wait(5)
        return 'fictional-wallet'
    monkeypatch.setattr('pbdesk.services.kwallet.authorize_database_wallet', authorize)
    async def run():
        app = create_app(settings, demo=True)
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.02)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                first = asyncio.create_task(client.post('/api/v1/history/protect', headers=headers(settings)))
                assert await asyncio.to_thread(entered.wait, 2)
                first.cancel()
                await asyncio.sleep(.03)
                assert app.state.history_lock.locked()
                second = asyncio.create_task(client.post('/api/v1/history/protect', headers=headers(settings)))
                await asyncio.sleep(.03)
                assert authorizations == [True]
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert (await second).status_code == 200
                assert len(wallet[0]) == 1
    asyncio.run(run())
