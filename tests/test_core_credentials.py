"""Credential-source selection and the narrow KWallet D-Bus adapter use fake secrets."""
import json
from types import SimpleNamespace

import pytest
from jeepney import HeaderFields

from pbdesk import credentials
from pbdesk.config import Settings
from pbdesk.services import kwallet


class FakeWalletBus:
    def __init__(self, **overrides):
        self.replies = {
            "ListNames": ["org.kde.kwalletd5", "org.kde.kwalletd6"],
            "networkWallet": "kdewallet", "wallets": ["kdewallet"],
            "open": 41, "hasFolder": True, "hasEntry": True,
            "readPassword": "fake-provider-token", "close": 0,
            **overrides,
        }
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send_and_get_reply(self, message, *, timeout):
        method = message.header.fields[HeaderFields.member]
        self.calls.append(message)
        assert 0 < timeout <= 5
        reply = self.replies[method]
        if isinstance(reply, Exception):
            raise reply
        return (reply,)


@pytest.fixture
def fake_bus(monkeypatch):
    bus = FakeWalletBus()
    monkeypatch.setattr(kwallet, "open_dbus_connection", lambda **kwargs: bus)
    monkeypatch.setattr(kwallet, "unwrap_msg", lambda reply: reply)
    return bus


def test_kwallet_reads_selected_folder_and_entry_and_releases_only_own_handle(fake_bus):
    assert kwallet.read_password("Pushbullet", "api-key") == "fake-provider-token"
    calls = {message.header.fields[HeaderFields.member]: message for message in fake_bus.calls}
    assert calls["open"].body == ("kdewallet", 0, "pbdesk")
    assert calls["open"].header.fields[HeaderFields.destination] == "org.kde.kwalletd6"
    assert calls["readPassword"].body == (41, "Pushbullet", "api-key", "pbdesk")
    assert calls["close"].body == (41, False, "pbdesk")
    assert not any(name.startswith(("write", "create", "remove")) for name in calls)


@pytest.mark.parametrize("missing", ["hasFolder", "hasEntry"])
def test_missing_entry_never_reads_another_password(fake_bus, missing):
    fake_bus.replies[missing] = False
    assert kwallet.read_password("Pushbullet", "api-key", "kdewallet") is None
    methods = [message.header.fields[HeaderFields.member] for message in fake_bus.calls]
    assert "readPassword" not in methods
    assert methods[-1] == "close"


def test_wallet_missing_does_not_create_or_open_it(fake_bus):
    with pytest.raises(kwallet.KWalletUnavailable):
        kwallet.read_password("Pushbullet", "api-key", "nonexistent-wallet")
    assert "open" not in [message.header.fields[HeaderFields.member] for message in fake_bus.calls]


def test_kwallet_denial_and_errors_are_sanitized(fake_bus):
    fake_bus.replies["readPassword"] = RuntimeError("fake-provider-token must not be logged")
    with pytest.raises(kwallet.KWalletUnavailable) as error:
        kwallet.read_password("Pushbullet", "api-key")
    assert "fake-provider-token" not in str(error.value)
    assert error.value.__suppress_context__
    fake_bus.replies["open"] = -1
    with pytest.raises(kwallet.KWalletUnavailable):
        kwallet.read_password("Pushbullet", "api-key")


def test_reference_contains_only_location_and_uses_exact_entry(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    calls = []
    def read(folder, entry, wallet):
        calls.append((folder, entry, wallet))
        return "fake-provider-token"
    monkeypatch.setattr(kwallet, "read_password", read)
    monkeypatch.setattr(credentials, "secure_backend", lambda: pytest.fail("Must not fall back to another store"))
    credentials.use_kwallet(settings, wallet="kdewallet")
    path = tmp_path / "provider-credential.json"
    assert json.loads(path.read_text()) == {
        "backend": "kwallet", "wallet": "kdewallet", "folder": "Pushbullet", "entry": "api-key",
    }
    assert "fake-provider-token" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert credentials.provider_token(settings) == "fake-provider-token"
    assert calls == [("Pushbullet", "api-key", "kdewallet")] * 2


def test_unavailable_selected_wallet_never_falls_back(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    monkeypatch.setattr(kwallet, "read_password", lambda *args: "fake-provider-token")
    credentials.use_kwallet(settings)
    def locked(*args):
        raise kwallet.KWalletUnavailable("unavailable")
    monkeypatch.setattr(kwallet, "read_password", locked)
    monkeypatch.setattr(credentials, "secure_backend", lambda: pytest.fail("No fallback allowed"))
    with pytest.raises(credentials.CredentialLocked):
        credentials.provider_token(settings)


def test_missing_entry_does_not_replace_existing_source(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    credentials._save_provider_source(settings, {"backend": "keyring"})
    monkeypatch.setattr(kwallet, "read_password", lambda *args: None)
    with pytest.raises(credentials.CredentialLocked):
        credentials.use_kwallet(settings)
    assert credentials.provider_credential_source(settings) == {"backend": "keyring"}


def test_legacy_keyring_and_explicit_token_setup_remain_supported(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    values = {("pbdesk", "pushbullet"): "old-fake-token"}
    backend = SimpleNamespace(
        get_password=lambda service, username: values[(service, username)],
        set_password=lambda service, username, token: values.__setitem__((service, username), token),
    )
    monkeypatch.setattr(credentials, "secure_backend", lambda: backend)
    assert credentials.provider_token(settings) == "old-fake-token"
    credentials._save_provider_source(settings, {"backend": "kwallet", "wallet": None, "folder": "Pushbullet", "entry": "api-key"})
    credentials.store_provider_token("new-fake-token", settings)
    assert credentials.provider_credential_source(settings) == {"backend": "keyring"}
    assert credentials.provider_token(settings) == "new-fake-token"
    assert "new-fake-token" not in (tmp_path / "provider-credential.json").read_text()


def test_invalid_reference_fails_closed(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    credentials._save_provider_source(settings, {"backend": "plaintext"})
    monkeypatch.setattr(credentials, "secure_backend", lambda: pytest.fail("Invalid source must not fall back"))
    with pytest.raises(credentials.CredentialLocked):
        credentials.provider_token(settings)


def test_diagnostics_source_excludes_unrecognized_fields(tmp_path):
    settings = Settings(tmp_path)
    credentials._save_provider_source(settings, {"backend": "keyring", "token": "fake-provider-token"})
    assert credentials.provider_credential_source(settings) == {"backend": "keyring"}
    credentials._save_provider_source(settings, {"backend": "kwallet", "wallet": None, "folder": "Pushbullet", "entry": "api-key", "token": "fake-provider-token"})
    assert "fake-provider-token" not in json.dumps(credentials.provider_credential_source(settings))


def test_local_kwallet_setup_cli_does_not_print_token(tmp_path, monkeypatch, capsys):
    from pbdesk.cli import admin_main
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings(tmp_path)))
    monkeypatch.setattr(kwallet, "read_password", lambda *args: "fake-provider-token")
    admin_main(["use-kwallet", "--folder", "Pushbullet", "--entry", "api-key"])
    assert "fake-provider-token" not in capsys.readouterr().out
    assert credentials.provider_credential_source(Settings(tmp_path))["entry"] == "api-key"


def test_e2e_password_uses_same_selected_kwallet_and_preserves_exact_unicode(tmp_path,monkeypatch):
    settings=Settings(tmp_path)
    credentials._save_provider_source(settings,{"backend":"kwallet","wallet":"kdewallet","folder":"Pushbullet","entry":"api-key"})
    calls=[]
    def read(folder,entry,wallet):
        calls.append((folder,entry,wallet));return " é exact password \n"
    monkeypatch.setattr(kwallet,"read_password",read)
    monkeypatch.setattr(credentials,"secure_backend",lambda:pytest.fail("Selected source must not fall back"))
    assert credentials.e2e_password(settings)==" é exact password \n"
    assert calls==[("Pushbullet","e2e-password","kdewallet")]
    assert "exact password" not in (tmp_path/"provider-credential.json").read_text()


def test_optional_keyring_e2e_entry_and_locked_state(tmp_path,monkeypatch):
    settings=Settings(tmp_path);calls=[]
    backend=SimpleNamespace(get_password=lambda service,name:calls.append((service,name)) or " pwd ")
    monkeypatch.setattr(credentials,"secure_backend",lambda:backend)
    assert credentials.e2e_password(settings)==" pwd "
    assert calls==[("pbdesk","pushbullet-e2e")]
    monkeypatch.setattr(credentials,"secure_backend",lambda:(_ for _ in ()).throw(RuntimeError("fake secret")))
    with pytest.raises(credentials.CredentialLocked) as error:credentials.e2e_password(settings)
    assert "fake secret" not in str(error.value)


def test_new_device_kwallet_entry_write_is_explicit_verified_and_never_overwrites(fake_bus):
    fake_bus.replies.update(hasEntry=False,writePassword=0,readPassword='fixture-new-password')
    kwallet.write_new_password('Pushbullet','phone-specific','fixture-new-password','kdewallet')
    calls={message.header.fields[HeaderFields.member]:message for message in fake_bus.calls}
    assert calls['writePassword'].body==(41,'Pushbullet','phone-specific','fixture-new-password','pbdesk')
    assert calls['close'].body==(41,False,'pbdesk')
    fake_bus.calls.clear();fake_bus.replies['hasEntry']=True
    with pytest.raises(kwallet.KWalletUnavailable):
        kwallet.write_new_password('Pushbullet','phone-specific','replacement','kdewallet')
    assert 'writePassword' not in [m.header.fields[HeaderFields.member] for m in fake_bus.calls]
    for entry in ('api-key','e2e-password'):
        fake_bus.calls.clear()
        with pytest.raises(kwallet.KWalletUnavailable):
            kwallet.write_new_password('Pushbullet',entry,'replacement','kdewallet')
        assert not fake_bus.calls


def test_simultaneous_kwallet_creators_cannot_overwrite_new_entry(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    state={};writes=[]
    class ConcurrentBus(FakeWalletBus):
        def send_and_get_reply(self,message,*,timeout):
            method=message.header.fields[HeaderFields.member]
            if method=='hasEntry':
                time.sleep(.02)
                return (message.body[2] in state,)
            if method=='writePassword':
                state[message.body[2]]=message.body[3];writes.append(message.body[3]);return (0,)
            if method=='readPassword':
                return (state.get(message.body[2]),)
            return super().send_and_get_reply(message,timeout=timeout)
    monkeypatch.setattr(kwallet,'open_dbus_connection',lambda **kwargs:ConcurrentBus())
    monkeypatch.setattr(kwallet,'unwrap_msg',lambda reply:reply)
    def create(password):
        try:
            kwallet.write_new_password('Pushbullet','same-new-entry',password,'kdewallet')
            return 'created'
        except kwallet.KWalletUnavailable:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,['first-fixture','second-fixture']))
    assert sorted(results)==['created','refused'] and len(writes)==1
    assert state['same-new-entry']==writes[0]
