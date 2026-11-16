"""Isolated encrypted test histories; no production raw-key environment switch."""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'history_bootstrap: exercise unprovisioned history security')


@pytest.fixture(autouse=True)
def encrypted_test_histories(monkeypatch, request):
    if request.node.get_closest_marker('history_bootstrap'):
        return
    from pbdesk.services import history_security, kwallet
    from pbdesk.storage.database import Store
    keys = {}
    monkeypatch.setattr(kwallet, 'authorize_database_wallet', lambda wallet=None: wallet or 'fixture-wallet')
    def write(identity, key, wallet):
        if identity in keys:
            raise AssertionError('Test key entries must never be overwritten')
        keys[identity] = key
    def read(identity, *, interactive=False):
        if identity not in keys:
            raise kwallet.KWalletUnavailable('Fixture key is missing')
        return keys[identity]
    monkeypatch.setattr(kwallet, 'write_database_key', write)
    monkeypatch.setattr(kwallet, 'read_database_key', read)
    original_security_init = history_security.HistorySecurity.__init__
    original_store_init = Store.__init__
    provisioning = False
    def security_init(self, settings):
        nonlocal provisioning
        original_security_init(self, settings)
        if not provisioning and self.inspect()['state'] == 'new':
            provisioning = True
            try:
                store = self.protect()
                self.finish_recovery_offer(store, saved=False)
                store.close()
                self._store, self._key = None, None
            finally:
                provisioning = False
    def store_init(self, settings, *, database_key=None, **kwargs):
        if database_key is None:
            history_security.HistorySecurity(settings)
        original_store_init(self, settings, database_key=database_key, **kwargs)
    monkeypatch.setattr(history_security.HistorySecurity, '__init__', security_init)
    monkeypatch.setattr(Store, '__init__', store_init)
    from history_client_helpers import wait_for_encrypted_test_client
    wait_for_encrypted_test_client(monkeypatch)
