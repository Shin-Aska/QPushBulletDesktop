"""Test-only child wallet adapter, injected through a temporary sitecustomize.

Production has no environment-key escape hatch. The fixture writes fictional keys
only beneath pytest's temporary directory and children inherit that test adapter.
"""
import json
import os
from pathlib import Path


def provision_process_history(settings, tmp_path, monkeypatch):
    from pbdesk.services import kwallet
    from pbdesk.services.history_security import HistorySecurity
    adapter = tmp_path / 'child-adapter'
    adapter.mkdir(exist_ok=True)
    source = '''import json
import os
from pathlib import Path
from pbdesk.services import kwallet
path = Path(__file__).with_name('fixture-wallet.json')
def authorize(wallet=None):
    return 'fictional-test-wallet'
def write(identity, key, wallet):
    keys = json.loads(path.read_text()) if path.exists() else {}
    if identity in keys:
        raise RuntimeError('Fixture key overwrite')
    keys[identity] = key.hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(keys, stream)
def read(identity, *, interactive=False):
    return bytes.fromhex(json.loads(path.read_text())[identity])
kwallet.authorize_database_wallet = authorize
kwallet.write_database_key = write
kwallet.read_database_key = read
'''
    (adapter / 'sitecustomize.py').write_text(source)
    # Execute the exact child implementation in a private namespace for initial
    # provisioning; monkeypatch restores parent adapters after this test.
    namespace = {'__file__': str(adapter / 'sitecustomize.py')}
    original = (kwallet.authorize_database_wallet, kwallet.write_database_key, kwallet.read_database_key)
    exec(compile(source, str(adapter / 'sitecustomize.py'), 'exec'), namespace)
    replacements = (namespace['authorize'], namespace['write'], namespace['read'])
    kwallet.authorize_database_wallet, kwallet.write_database_key, kwallet.read_database_key = original
    for name, replacement in zip(('authorize_database_wallet', 'write_database_key', 'read_database_key'), replacements):
        monkeypatch.setattr(kwallet, name, replacement)
    security = HistorySecurity(settings)
    store = security.open_existing()
    security.finish_recovery_offer(store, saved=False)
    store.close()
    package = str(Path(__file__).resolve().parents[1] / 'src')
    monkeypatch.setenv('PYTHONPATH', str(adapter) + os.pathsep + package)
